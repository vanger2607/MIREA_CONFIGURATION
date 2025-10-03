import os
import re
import socket
import getpass
import shlex
import sys
import argparse
from typing import Dict, List, Tuple
from collections import Counter
import csv
import base64
import posixpath as ppath

# Aliases:
ArgList = List[str]
PositionArgsList = List[str]
OptionsList = List[str]
OptionName = str
OptionTakesValue = bool
IntOrNone = int | None
StringOrNone = str | None


class CommandError(Exception):
    pass


class EnvironmentVariableNotFoundError(CommandError):
    def __init__(self, var: str):
        super().__init__(f"environment variable not found: {var}")
        self.var = var


class InvalidArgumentsError(CommandError):
    pass


class InvalidOptionError(CommandError):
    pass


class ExecutionError(CommandError):
    pass


def get_user_info(vfs_path: StringOrNone = None, vfs_cwd: StringOrNone = None) -> str:
    """
    Create the prompt string.
    """
    user = getpass.getuser()
    host = socket.gethostname()
    if vfs_path:
        cwd = vfs_cwd or "/"
        return f"{user}@{host}:[vfs:{vfs_path}]{cwd} "
    else:
        cwd = os.getcwd()
        return f"{user}@{host}:{cwd}$ "


def expand_tokenen(tokenen: str) -> str:
    expanded = os.path.expanduser(tokenen)
    pattern = re.compile(r"\$(\w+)|\$\{([^}]+)\}")
    matches = pattern.findall(expanded)
    for m in matches:
        var = m[0] or m[1]
        if var not in os.environ:
            raise EnvironmentVariableNotFoundError(var)
    return os.path.expandvars(expanded)


# VFS
class VFSNode:
    def __init__(self, name: str, node_type: str = "dir"):
        self.name = name
        self.type = node_type  # 'dir', 'file', 'link'
        self.children: Dict[str, "VFSNode"] = {}
        self.content: bytes = b""
        self.link_target: str | None = None

    def is_dir(self) -> bool:
        return self.type == "dir"

    def is_file(self) -> bool:
        return self.type == "file"

    def is_link(self) -> bool:
        return self.type == "link"


class VirtualFileSystem:
    """
    Build a tree from a CSV with columns path,type,content_base64.
    Supports absolute paths like /a/b/c.
    """

    def __init__(self) -> None:
        self.root = VFSNode("/", "dir")

    def normalize_vfs_path(self, path: str) -> str:
        """Normalize slashes and apply POSIX normalization rules."""
        if path is None:
            raise ValueError("path is None")
        p = path.replace("\\", "/").strip()
        # posix normpath: "/." -> "/", "/a//b" -> "/a/b", "///a" -> "/a"
        norm = ppath.normpath(p)
        # normpath may return "" for empty string — convert to "/"
        if norm == "":
            norm = "/"
        if not norm.startswith("/"):
            raise ValueError("VFS: path must be absolute")
        return norm

    def split_path(self, path: str) -> List[str]:
        """
        Split an absolute VFS path into segments.
        """
        norm = self.normalize_vfs_path(path)
        if norm == "/":
            return []
        parts = [part for part in norm.split('/') if part]
        return parts

    def add_node(self, path: str, node_type: str, content_b64: str | None = None, overwrite: bool = False) -> None:
        """
        Add a node to the VFS at an absolute path.
        """
        if node_type not in {"dir", "file", "link"}:
            raise ValueError(f"invalid node_type: {node_type!r}")

        parts = self.split_path(path)
        # If path is root:
        if not parts:
            if node_type != "dir":
                raise ValueError("cannot create non-dir at root")
            return

        cur = self.root
        for idx, part in enumerate(parts[:-1]):
            child = cur.children.get(part)
            if child is None:
                # create intermediate directory
                child = VFSNode(part, "dir")
                cur.children[part] = child
            else:
                # if existing node is not a directory, conflict
                if child.type != "dir":
                    if not overwrite:
                        raise ValueError(f"intermediate path conflict at '/{'/'.join(parts[:idx+1])}': existing type={child.type}, expected dir")
                    else:
                        # overwrite as directory
                        child.type = "dir"
                        child.children = {}
                        child.content = b""
                        child.link_target = None
            cur = child

        last = parts[-1]
        node = cur.children.get(last)
        if node is None:
            node = VFSNode(last, node_type)
            cur.children[last] = node
        else:
            existing_type = getattr(node, "type", None)
            if existing_type and existing_type != node_type:
                if not overwrite:
                    raise ValueError(f"add_node: conflict at '{path}': existing type={existing_type}, new type={node_type}")
                else:
                    node.type = node_type
                    node.children = {}
                    node.content = b""
                    node.link_target = None

        # fill fields
        if node_type == "file":
            if content_b64:
                try:
                    node.content = base64.b64decode(content_b64)
                except (base64.binascii.Error, TypeError) as e:
                    raise ValueError(f"invalid base64 content for file at '{path}': {e}")
            else:
                node.content = b""
        elif node_type == "link":
            if content_b64:
                try:
                    decoded = base64.b64decode(content_b64)
                except (base64.binascii.Error, TypeError) as e:
                    raise ValueError(f"invalid base64 content for link at '{path}': {e}")
                try:
                    node.link_target = decoded.decode("utf-8")
                except UnicodeDecodeError as e:
                    raise ValueError(f"link target is not valid UTF-8 at '{path}': {e}")
            else:
                node.link_target = None
        # dir — nothing else to fill

    def load_from_csv(self, csv_path: str) -> None:
        """Load VFS from CSV (expects headers path,type,content_base64)."""
        if not os.path.exists(csv_path):
            raise FileNotFoundError(csv_path)
        with open(csv_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            required = {"path", "type", "content_base64"}
            fieldnames = reader.fieldnames or []
            field_set = set(fieldnames)
            missing = required - field_set
            if missing:
                raise ValueError(f"CSV missing required headers: {', '.join(sorted(missing))}")
            for row in reader:
                raw_path = row['path']
                # Normalize the path from CSV (replace '\' -> '/')
                try:
                    norm_path = raw_path.replace("\\", "/")
                except Exception:
                    norm_path = raw_path
                node_type = row['type']
                content_b64 = row.get('content_base64') or None
                self.add_node(norm_path, node_type, content_b64, overwrite=True)

    def resolve_node(self, path: str, visited: set | None = None) -> VFSNode:
        """Find a node by absolute path, resolving symlinks."""
        if visited is None:
            visited = set()
        norm = self.normalize_vfs_path(path)
        if norm == "/":
            return self.root
        parts = [part for part in norm.split("/") if part]
        cur = self.root
        processed_parts: List[str] = []
        for part in parts:
            if part not in cur.children:
                raise FileNotFoundError(path)
            cur = cur.children[part]
            processed_parts.append(part)
            if cur.is_link():
                target = cur.link_target
                if not target:
                    raise FileNotFoundError(f"broken symlink at {'/'.join(processed_parts)}")
                # normalize relative link relative to parent
                tgt = target.replace("\\", "/")
                if not tgt.startswith('/'):
                    parent = '/' + '/'.join(processed_parts[:-1]) if processed_parts[:-1] else '/'
                    tgt = ppath.normpath(parent.rstrip('/') + '/' + tgt)
                else:
                    tgt = ppath.normpath(tgt)
                if tgt in visited:
                    raise RecursionError(f"symlink loop detected at {tgt}")
                visited.add(tgt)
                cur = self.resolve_node(tgt, visited=visited)
        return cur

    def listdir(self, path: str) -> List[str]:
        node = self.resolve_node(path)
        if not node.is_dir():
            raise NotADirectoryError(path)
        return sorted(node.children.keys())

    def read_file(self, path: str) -> bytes:
        node = self.resolve_node(path)
        if node.is_dir():
            raise IsADirectoryError(path)
        return node.content

    def exists(self, path: str) -> bool:
        try:
            self.resolve_node(path)
            return True
        except FileNotFoundError:
            return False

    def remove_node(self, path: str, recursive: bool = False) -> None:
        """
        Remove a node at path. If it's a directory and not empty — raise IsADirectoryError.
        If recursive=True — remove recursively.
        """
        norm = self.normalize_vfs_path(path)
        if norm == "/":
            raise ValueError("cannot remove root directory")
        parts = self.split_path(norm)
        if not parts:
            raise FileNotFoundError(path)
        parent = self.root
        for p in parts[:-1]:
            child = parent.children.get(p)
            if child is None:
                raise FileNotFoundError(path)
            if not child.is_dir():
                raise NotADirectoryError(f"parent component is not a directory: {p}")
            parent = child
        last = parts[-1]
        node = parent.children.get(last)
        if node is None:
            raise FileNotFoundError(path)
        if node.is_dir() and node.children and not recursive:
            raise IsADirectoryError(f"directory not empty: {path}")
        # recursive deletion — just remove reference to node (GC will clear subtree)
        del parent.children[last]

    def debug_tree(self, print_fn=print) -> None:
        """Print the VFS structure (tree) for debugging."""

        def repr_node(node: VFSNode) -> str:
            if node.is_dir():
                return f"{node.name}/"
            elif node.is_file():
                return f"{node.name} (file, {len(node.content)} bytes)"
            else:
                return f"{node.name} -> {node.link_target or '<broken>'}"

        def walk(node: VFSNode, prefix: str) -> None:
            # print current node (for root show '/')
            if node is self.root:
                print("/")
            else:
                print(prefix + repr_node(node))
            if node.is_dir():
                for child_name in sorted(node.children.keys()):
                    child = node.children[child_name]
                    walk(child, prefix + "   / ")

        walk(self.root, "")


class Command:
    name: str = ""
    allowed_options: Dict[OptionName, OptionTakesValue] = {}
    min_args: int = 0
    max_args: IntOrNone = None
    description: str = ""

    def __init__(self) -> None:
        if not self.name:
            raise ValueError("Command subclass must set 'name' attribute")
        self.repl: 'REPL' | None = None

    def parse(self, argv: ArgList) -> Tuple[PositionArgsList, Dict[str, str | bool]]:
        """
        Parse argv and return (positionals, options_dict).

        options_dict: key -> True (for flag) or string (for option with value).
        Supports:
          - single short opts: -a
          - combined short opts: -la -> -l=True, -a=True
          - short opt with argument: -fVALUE or -f VALUE
          - long opts: --name or --name=value (value may be empty string)
        """
        options: Dict[str, str | bool] = {}
        positionals: List[str] = []

        i = 0
        while i < len(argv):
            token = argv[i]
            if token == "-":
                # treat single dash as positional (e.g., stdin)
                positionals.append(token)
                i += 1
                continue

            if token.startswith("--"):
                # long option: --name or --name=value
                if "=" in token:
                    name, val = token.split("=", 1)
                else:
                    name, val = token, True
                # store as is
                options[name] = val
                i += 1
                continue

            if token.startswith("-") and len(token) > 1:
                # short options or short-with-value
                # example: -abc  -> -a -b -c
                # example: -fVALUE or -f VALUE -> -f: "VALUE" (only if -f takes value)
                # iterate characters after first '-'
                j = 1
                consumed_value = False
                while j < len(token):
                    ch = token[j]
                    opt = "-" + ch
                    takes_value = bool(self.allowed_options.get(opt, False))
                    # validate short options if allowed_options is non-empty
                    if self.allowed_options and opt not in self.allowed_options:
                        raise InvalidOptionError(f"{self.name}: invalid option: {opt}")
                    if takes_value:
                        # rest of token after this char is the value, if non-empty; otherwise look to next argv
                        val = token[j+1:]  # may be empty
                        if val == "":
                            # try next argv token
                            i += 1
                            if i >= len(argv):
                                raise InvalidArgumentsError(f"{self.name}: option {opt} requires an argument")
                            val = argv[i]
                        options[opt] = val
                        consumed_value = True
                        break  # stop scanning chars in current token, we've consumed value
                    else:
                        options[opt] = True
                        j += 1
                i += 1
                if consumed_value:
                    i += 0  # already advanced to next arg if we took it from argv
                continue

            # otherwise positional
            positionals.append(token)
            i += 1

        # validate positional counts
        if self.max_args is not None and len(positionals) > self.max_args:
            raise InvalidArgumentsError(f"{self.name}: too many arguments (max {self.max_args})")
        if len(positionals) < self.min_args:
            raise InvalidArgumentsError(f"{self.name}: too few arguments (min {self.min_args})")

        return positionals, options

    def execute(self, argv: ArgList) -> None:
        raise NotImplementedError("Command.execute must be implemented")


class LsCommand(Command):
    name = "ls"
    allowed_options: Dict[OptionName, OptionTakesValue] = {
        "-a": False, "-l": False, "-h": False, "-R": False, "-t": False, "-r": False, "-S": False
    }
    min_args = 0
    max_args = None
    description = "List directory contents (VFS or real FS). Flags: -a show hidden, -l long listing, -h human-readable sizes, -R recurse, -r reverse, -S sort by size."


    def human_size(self, n: int) -> str:
        for unit in ("B", "K", "M", "G"):
            if n < 1024:
                return f"{n}{unit}"
            n = n // 1024
        return f"{n}T"

    def list_dir_vfs(self, vfs: VirtualFileSystem, abs_path: str, options: List[str], out_lines: List[str], header: str | None = None):
        try:
            node = vfs.resolve_node(abs_path)
        except FileNotFoundError:
            out_lines.append(f"ls: cannot access '{abs_path}': No such file or directory")
            return
        except Exception as e:
            out_lines.append(f"ls: error accessing '{abs_path}': {e}")
            return

        if not node.is_dir():
            name = ppath.basename(abs_path)
            if "-l" in options:
                size = len(node.content) if node.is_file() else 0
                size_str = self.human_size(size) if "-h" in options else str(size)
                type = "file" if node.is_file() else node.type
                out_lines.append(f"{type:6} {size_str:8} {name}")
            else:
                out_lines.append(name)
            return

        if header:
            out_lines.append(f"{header}:")

        entries = []
        for child_name, child_node in node.children.items():
            if child_name.startswith(".") and "-a" not in options:
                continue
            size = len(child_node.content) if child_node.is_file() else 0
            entries.append((child_name, child_node, size))

        # Sorting
        if "-S" in options:
            entries.sort(key=lambda x: -x[2])
        else:
            entries.sort(key=lambda x: x[0])

        if "-r" in options:
            entries.reverse()

        if "-l" in options:
            for name, nd, size in entries:
                type = "dir" if nd.is_dir() else ("link" if nd.is_link() else "file")
                size_str = self.human_size(size) if "-h" in options else str(size)
                out_lines.append(f"{type:6} {size_str:8} {name}")
        else:
            if entries:
                out_lines.append("  ".join(name for name, _, _ in entries))

        # recursion
        if "-R" in options:
            for name, nd, _ in entries:
                if nd.is_dir():
                    child_path = abs_path.rstrip("/") + "/" + name if abs_path != "/" else "/" + name
                    self.list_dir_vfs(vfs, child_path, options, out_lines, header=child_path)

    def execute(self, argv: ArgList) -> None:
        positional, options = self.parse(argv)
        repl = self.repl
        if repl is None:
            raise ExecutionError("ls: no REPL context")

        targets = positional if positional else ["."]
        out_lines: List[str] = []
        multiple = len(targets) > 1

        for t in targets:
            if repl.vfs:
                try:
                    abs_path = repl.to_vfs_abs(t)
                except Exception as e:
                    out_lines.append(f"ls: cannot access '{t}': {e}")
                    continue
                header = abs_path if multiple else None
                self.list_dir_vfs(repl.vfs, abs_path, options, out_lines, header=header)
            else:
                path = os.path.expanduser(t)
                if not os.path.exists(path):
                    out_lines.append(f"ls: cannot access '{t}': No such file or directory")
                    continue
                if os.path.isfile(path):
                    name = os.path.basename(path)
                    if "-l" in options:
                        size = os.path.getsize(path)
                        size_str = self.human_size(size) if "-h" in options else str(size)
                        out_lines.append(f"file   {size_str:8} {name}")
                    else:
                        out_lines.append(name)
                else:
                    entries = os.listdir(path)
                    if "-a" not in options:
                        entries = [e for e in entries if not e.startswith(".")]
                    if "-S" in options:
                        entries.sort(key=lambda e: os.path.getsize(os.path.join(path, e)) if os.path.exists(os.path.join(path, e)) else 0, reverse=True)
                    else:
                        entries.sort()
                    if "-r" in options:
                        entries.reverse()
                    if "-l" in options:
                        for e in entries:
                            full = os.path.join(path, e)
                            typ = "dir" if os.path.isdir(full) else "file"
                            size = os.path.getsize(full) if os.path.isfile(full) else 0
                            size_str = self.human_size(size) if "-h" in options else str(size)
                            out_lines.append(f"{typ:6} {size_str:8} {e}")
                    else:
                        if entries:
                            out_lines.append("  ".join(entries))

        for l in out_lines:
            print(l)


class CdCommand(Command):
    name = "cd"
    allowed_options: Dict[OptionName, OptionTakesValue] = {}
    min_args = 0
    max_args = 1
    description = "Change current directory (VFS or real FS). Accepts one path (absolute or relative); supports '.' and '..'."

    def execute(self, argv: ArgList) -> None:
        positional, options = self.parse(argv)
        repl = self.repl
        if repl is None:
            raise ExecutionError("cd: no REPL context")

        target = positional[0] if positional else "/"
        if repl.vfs:
            try:
                abs_path = repl.to_vfs_abs(target)
            except Exception as e:
                print(f"cd: {e}")
                return
            try:
                node = repl.vfs.resolve_node(abs_path)
            except FileNotFoundError:
                print(f"cd: no such file or directory: {target}")
                return
            if not node.is_dir():
                print(f"cd: not a directory: {target}")
                return
            repl.vfs_cwd = abs_path
        else:
            try:
                os.chdir(os.path.expanduser(target))
            except Exception as e:
                print(f"cd: {e}")


class ExitCommand(Command):
    name = "exit"
    allowed_options: Dict[OptionName, OptionTakesValue] = {}
    min_args = 0
    max_args = 0
    description = "Exit the REPL."

    def execute(self, argv: ArgList) -> None:
        self.parse(argv)
        print("exit called — exiting")
        sys.exit(0)


class WcCommand(Command):
    name = "wc"
    allowed_options: Dict[OptionName, OptionTakesValue] = {}
    min_args = 1
    max_args = None
    description = "Count lines, words and bytes of files (VFS or real FS)."

    def count_bytes_words_lines(self, data: bytes) -> Tuple[int, int, int]:
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        nlines = len(lines)
        words = 0
        for line in lines:
            words += len(line.split())
        nbytes = len(data)
        return nlines, words, nbytes

    def execute(self, argv: ArgList) -> None:
        positional, options = self.parse(argv)
        repl = self.repl
        targets = positional if positional else []
        if not targets:
            print("wc: no files specified")
            return

        for t in targets:
            if repl and repl.vfs:
                try:
                    abs_path = repl.to_vfs_abs(t)
                    data = repl.vfs.read_file(abs_path)
                except FileNotFoundError:
                    print(f"wc: {t}: No such file")
                    continue
                except IsADirectoryError:
                    print(f"wc: {t}: Is a directory")
                    continue
            else:
                path = os.path.expanduser(t)
                if not os.path.exists(path):
                    print(f"wc: {t}: No such file")
                    continue
                if os.path.isdir(path):
                    print(f"wc: {t}: Is a directory")
                    continue
                with open(path, "rb") as f:
                    data = f.read()
            lines, words, nbytes = self.count_bytes_words_lines(data)
            print(f"{lines:7} {words:7} {nbytes:7} {t}")


class UniqCommand(Command):
    name = "uniq"
    allowed_options: Dict[OptionName, OptionTakesValue] = {
        "-c": False, "-d": False, "-u": False, "-A": False, "-f": True, "-s": True, "-i": False
    }
    min_args = 1
    max_args = 1
    description = "Filter duplicate lines. Options: -c prefix counts, -d show only duplicates, -u show only uniques, -A/--all global (not only adjacent), -f N ignore first N fields, -s N skip first N chars, -i ignore case. Use 'help uniq' for examples."

    def execute(self, argv: ArgList) -> None:
        positionals, opts = self.parse(argv)
        path = positionals[0]

        show_counts = bool(opts.get("-c", False))
        only_duplicates = bool(opts.get("-d", False))
        only_unique = bool(opts.get("-u", False))
        global_mode = bool(opts.get("-A") or opts.get("--all"))
        ignore_case  = bool(opts.get("-i"))

        skip_lines = 0
        skip_chars = 0
        if "-f" in opts and opts["-f"] is not True:
            try:
                skip_lines = int(opts["-f"])
            except Exception:
                raise InvalidArgumentsError("uniq: invalid argument for -f")
        if "-s" in opts and opts["-s"] is not True:
            try:
                skip_chars = int(opts["-s"])
            except Exception:
                raise InvalidArgumentsError("uniq: invalid argument for -s")

        if only_duplicates and only_unique:
            print("conflict -d an -u can't be ised together")
            return

        # read file
        repl = self.repl
        try:
            if repl and repl.vfs:
                abs_path = repl.to_vfs_abs(path)
                data_bytes = repl.vfs.read_file(abs_path)
            else:
                with open(os.path.expanduser(path), "rb") as f:
                    data_bytes = f.read()
        except FileNotFoundError:
            print(f"uniq: {path}: No such file")
            return
        except IsADirectoryError:
            print(f"uniq: {path}: Is a directory")
            return

        text = data_bytes.decode("utf-8", errors="replace")
        lines = text.splitlines()

        head = lines[:skip_lines] if skip_lines > 0 else []
        tail = lines[skip_lines:] if skip_lines > 0 else lines

        for h in head:
            print(h)
        if not tail:
            return

        def cmp_key(line: str) -> str:
            k = line if skip_chars <= 0 else ("" if len(line) <= skip_chars else line[skip_chars:])
            return k.lower() if ignore_case else k

        if global_mode:
            keys = [cmp_key(l) for l in tail]
            counts = Counter(keys)
            seen = set()
            for orig_line, k in zip(tail, keys):
                if k in seen:
                    continue
                cnt = counts[k]
                if only_duplicates and cnt <= 1:
                    seen.add(k); continue
                if only_unique and cnt != 1:
                    seen.add(k); continue
                if show_counts:
                    print(f"{cnt:7} {orig_line}")
                else:
                    print(orig_line)
                seen.add(k)
        else:
            prev = tail[0]; prev_key = cmp_key(prev); cnt = 1
            for cur in tail[1:]:
                cur_key = cmp_key(cur)
                if cur_key == prev_key:
                    cnt += 1
                else:
                    if only_duplicates:
                        if cnt > 1:
                            print(f"{cnt:7} {prev}" if show_counts else prev)
                    elif only_unique:
                        if cnt == 1:
                            print(prev)
                    else:
                        print(f"{cnt:7} {prev}" if show_counts else prev)
                    prev = cur; prev_key = cur_key; cnt = 1
            if only_duplicates:
                if cnt > 1:
                    print(f"{cnt:7} {prev}" if show_counts else prev)
            elif only_unique:
                if cnt == 1:
                    print(prev)
            else:
                print(f"{cnt:7} {prev}" if show_counts else prev)


class PwdCommand(Command):
    name = "pwd"
    allowed_options: Dict[OptionName, OptionTakesValue] = {}
    min_args = 0
    max_args = 0
    description = "Print working directory (VFS or real FS)."

    def execute(self, argv: ArgList) -> None:
        self.parse(argv)
        repl = self.repl
        if repl and repl.vfs:
            print(repl.vfs_cwd or "/")
        else:
            print(os.getcwd())


class RmCommand(Command):
    name = "rm"
    allowed_options: Dict[OptionName, OptionTakesValue] = {"-r": False, "-i": False, "-d": False, "--dir": False}
    min_args = 1
    max_args = None
    description = "Remove files or directories from the mounted VFS (in-memory). Options: -r recursive, -i interactive prompt, -d/--dir remove empty directories only. (Operates on VFS only when mounted.)"
    def execute(self, argv: ArgList) -> None:
        positionals, opts = self.parse(argv)
        repl = self.repl
        if repl is None:
            raise ExecutionError("rm: no REPL context")
        if not repl.vfs:
            print("rm: operation supported only on mounted VFS (in-memory).")
            return
        recursive = bool(opts.get("-r", False))
        interactive = bool(opts.get("-i", False))
        dir_flag = bool(opts.get("-d", False) or opts.get("--dir", False))

        for p in positionals:
            try:
                abs_path = repl.to_vfs_abs(p)
            except Exception as e:
                print(f"rm: {p}: {e}")
                continue

            # resolve existence
            try:
                node = repl.vfs.resolve_node(abs_path)
            except FileNotFoundError:
                print(f"rm: cannot remove '{p}': No such file or directory")
                continue
            except Exception as e:
                print(f"rm: error resolving '{p}': {e}")
                continue

            # directory handling
            if node.is_dir():
                # If -r specified -> remove recursively (after optional prompt)
                if recursive:
                    if interactive:
                        try:
                            ans = input(f"rm: descend into directory '{p}'? [y/N] ")
                        except EOFError:
                            ans = ""
                        if ans.lower() not in ("y", "yes"):
                            continue
                    try:
                        repl.vfs.remove_node(abs_path, recursive=True)
                    except Exception as e:
                        print(f"rm: error removing '{p}': {e}")
                    continue

                # Not recursive
                if dir_flag:
                    # try to remove empty directory
                    if interactive:
                        try:
                            ans = input(f"rm: remove directory '{p}'? [y/N] ")
                        except EOFError:
                            ans = ""
                        if ans.lower() not in ("y", "yes"):
                            continue
                    try:
                        repl.vfs.remove_node(abs_path, recursive=False)
                    except IsADirectoryError:
                        print(f"rm: cannot remove '{p}': Directory not empty")
                    except Exception as e:
                        print(f"rm: error removing '{p}': {e}")
                else:
                    print(f"rm: cannot remove '{p}': Is a directory (use -r to remove recursively)")
                continue

            # file or link
            if interactive:
                try:
                    ans = input(f"rm: remove '{p}'? [y/N] ")
                except EOFError:
                    ans = ""
                if ans.lower() not in ("y", "yes"):
                    continue
            try:
                repl.vfs.remove_node(abs_path, recursive=False)
            except Exception as e:
                print(f"rm: error removing '{p}': {e}")


class MkdirCommand(Command):
    name = "mkdir"
    description = (
        "Create directories in the mounted VFS (in-memory).\n"
        "Options:\n"
        "  -p              create parent directories as needed\n"
        "  -v, --verbose   print a message for each created directory\n"
        "\n"
        "Usage:\n"
        "  mkdir dir1 dir2 ...        # create multiple directories\n"
        "  mkdir -p /a/b/c            # create all missing parents\n"
        "  mkdir -v new_dir           # print message after creation\n"
    )

    def execute(self, args: list[str]):
        """Implements mkdir with -p and -v flags (UNIX-like)."""
        if not self.vfs:
            print("No VFS mounted. Cannot create directories.")
            return 1

        import argparse
        parser = argparse.ArgumentParser(prog="mkdir", add_help=False)
        parser.add_argument("-p", action="store_true", dest="parents")
        parser.add_argument("-v", "--verbose", action="store_true", dest="verbose")
        parser.add_argument("paths", nargs="*")
        try:
            opts = parser.parse_args(args)
        except SystemExit:
            # argparse throws SystemExit if parsing fails
            return 1

        if not opts.paths:
            print("mkdir: missing operand")
            return 1

        for raw_path in opts.paths:
            path = self.vfs.resolve_path(raw_path)

            # Case 1: -p — create all parent directories
            if opts.parents:
                created = self.vfs.make_dirs(path)  # assumed to create all parents
                if created and opts.verbose:
                    for p in created:
                        print(f"mkdir: created directory '{p}'")
                continue

            # Case 2: normal mode — no -p
            parent = self.vfs.dirname(path)
            if not self.vfs.exists(parent):
                print(f"mkdir: cannot create directory '{path}': No such file or directory")
                continue

            if self.vfs.exists(path):
                print(f"mkdir: cannot create directory '{path}': File exists")
                continue

            success = self.vfs.make_dir(path)
            if success:
                if opts.verbose:
                    print(f"mkdir: created directory '{path}'")
            else:
                print(f"mkdir: failed to create directory '{path}'")

        return 0


class HelpCommand(Command):
    name = "help"
    allowed_options: Dict[OptionName, OptionTakesValue] = {}
    min_args = 0
    max_args = None
    description = "Show help for available commands."

    def execute(self, argv: ArgList) -> None:
        positionals, opts = self.parse(argv)
        repl = self.repl
        if repl is None or getattr(repl, "registry", None) is None:
            print("help: no command registry available")
            return
        registry = repl.registry

        if not positionals:
            print("Available commands:")
            for name in registry.names():
                cmd = registry.get(name)
                desc = getattr(cmd, "description", "")
                print(f"{name:10} - {desc}")
        else:
            for name in positionals:
                cmd = registry.get(name)
                if not cmd:
                    print(f"help: no such command: {name}")
                else:
                    print(f"{name} - {getattr(cmd, 'description', 'No description')}")


class CommandRegistry:
    def __init__(self) -> None:
        self.commands: Dict[str, Command] = {}

    def register(self, command: Command) -> None:
        self.commands[command.name] = command

    def get(self, name: str) -> Command | None:
        return self.commands.get(name)

    def names(self) -> List[str]:
        return sorted(self.commands.keys())


class REPL:
    def __init__(self, registry: CommandRegistry, vfs_path: StringOrNone = None) -> None:
        self.registry = registry
        self.vfs_path = vfs_path
        self.vfs: VirtualFileSystem | None = None
        self.vfs_cwd: str | None = None
        # give commands access to vfs
        for cmd in self.registry.commands.values():
            cmd.repl = self
        # if vfs path is provided, mount it (expect CSV)
        if vfs_path:
            try:
                vfs = VirtualFileSystem()
                vfs.load_from_csv(vfs_path)
                self.vfs = vfs
                self.vfs_cwd = "/"
                print(f"VFS successfully mounted from {vfs_path}")
                print("VFS structure:")
                self.vfs.debug_tree(print_fn=print)
            except Exception as e:
                print(f"failed to mount vfs from {vfs_path}: {e}")
                sys.exit(1)

    def to_vfs_abs(self, path: str) -> str:
        """
        Convert an input path (absolute or relative) to an absolute VFS path.
        Handles: '.', '..', multiple slashes, backslashes.
        """
        if not self.vfs:
            raise RuntimeError("no vfs mounted")
        if path is None:
            return self.vfs_cwd or "/"
        p = path.replace("\\", "/").strip()
        if p == "":
            return self.vfs_cwd or "/"
        if p.startswith("/"):
            norm = ppath.normpath(p)
            if norm == "":
                norm = "/"
            if not norm.startswith("/"):
                norm = "/" + norm
            return norm
        else:
            base = self.vfs_cwd or "/"
            joined = ppath.join(base, p)
            norm = ppath.normpath(joined)
            if norm == "":
                norm = "/"
            if not norm.startswith("/"):
                norm = "/" + norm
            return norm

    def run_interactive(self) -> None:
        while True:
            prompt = get_user_info(self.vfs_path, self.vfs_cwd)
            print(prompt, end="")
            try:
                line = input()
            except EOFError:
                print()
                return

            if not line.strip():
                continue

            try:
                try:
                    tokenens = shlex.split(line, posix=True)
                except Exception as e:
                    raise CommandError(f"parse error: {e}")

                expanded = []
                for t in tokenens:
                    try:
                        expanded.append(expand_tokenen(t))
                    except EnvironmentVariableNotFoundError as ev:
                        print("error:", ev)
                        expanded = None
                        break
                if expanded is None:
                    continue

                command_name, *args = expanded
                command = self.registry.get(command_name)
                if command is None:
                    raise CommandError(f"command not found: {command_name}")
                command.execute(args)

            except CommandError as ce:
                print("error:", ce)
            except Exception as e:
                print("unexpected error:", e)

    def run_script(self, path: str) -> bool:
        """
        Execute a script. Returns True on successful completion,
        False if the script is aborted due to an error (process does NOT exit).
        """
        if not path:
            print("no script path provided")
            raise FileNotFoundError(path)
        if not os.path.exists(path):
            print(f"script not found: {path}")
            raise FileNotFoundError(path)

        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            print(f"cannot read script '{path}': {e}")
            raise

        for lineno, raw in enumerate(lines, start=1):
            line = raw.rstrip("\r\n")

            if not line.strip() or line.lstrip().startswith("#"):
                continue

            prompt = get_user_info(self.vfs_path, self.vfs_cwd)
            print(prompt + line)

            # 1) parse
            try:
                tokenens = shlex.split(line, posix=True)
            except Exception as e:
                print(f"error in script {path} at line {lineno}: parse error: {e}")
                return False

            # 2) expand variables
            expanded = []
            try:
                for t in tokenens:
                    expanded.append(expand_tokenen(t))
            except EnvironmentVariableNotFoundError as ev:
                print(f"error in script {path} at line {lineno}: {ev}")
                return False

            if not expanded:
                continue

            # 3) lookup command
            command_name, *args = expanded
            command = self.registry.get(command_name)
            if command is None:
                print(f"error in script {path} at line {lineno}: command not found: {command_name}")
                return False

            # 4) execute
            try:
                command.execute(args)
            except CommandError as ce:
                print(f"error in script {path} at line {lineno}: {ce}")
                return False
            except SystemExit:
                raise
            except Exception as e:
                print(f"unexpected error in script {path} at line {lineno}: {e}")
                return False

        return True


def make_default_registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(LsCommand())
    registry.register(CdCommand())
    registry.register(ExitCommand())
    registry.register(WcCommand())
    registry.register(UniqCommand())
    registry.register(PwdCommand())
    registry.register(RmCommand())
    registry.register(MkdirCommand())
    registry.register(HelpCommand())
    return registry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vfs", "-v", dest="vfs_path", default=None, help="Path to VFS CSV (mount in-memory)")
    parser.add_argument("--script", "-s", dest="script", default=None, help="Path to startup script")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("Starting emulator with parameters:")
    print(f" argv: {sys.argv}")
    print(f" vfs_path: {args.vfs_path}")
    print(f" script: {args.script}")
    print(f" process cwd: {os.getcwd()}")

    command_registry = make_default_registry()
    repl = REPL(command_registry, vfs_path=args.vfs_path)

    if args.script:
        try:
            ok = repl.run_script(args.script)
        except FileNotFoundError:
            print(f"start script not found: {args.script}")
            sys.exit(1)
        except SystemExit:
            raise
        except Exception as e:
            print(f"error while executing start script: {e}")
            ok = False

        if not ok:
            print(f"start script {args.script} terminated with errors; dropping to interactive REPL")
        else:
            print(f"start script {args.script} finished successfully")

    repl.run_interactive()


if __name__ == "__main__":
    main()
