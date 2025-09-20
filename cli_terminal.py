import os
import re
import socket
import getpass
import shlex
import sys
import argparse
from typing import Dict, List, Tuple
import csv
import base64

# Алиасы:
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
    Создаёт приглашение.
    """
    user = getpass.getuser()
    host = socket.gethostname()
    if vfs_path:
        cwd = vfs_cwd or "/"
        return f"{user}@{host}:[vfs:{vfs_path}]{cwd}$ "
    else:
        cwd = os.getcwd()
        return f"{user}@{host}:{cwd}$ "


def expand_token(token: str) -> str:
    expanded = os.path.expanduser(token)
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
    Строим дерево на основе CSV с колонками path,type,content_base64.
    Поддерживает абсолютные пути вида /a/b/c.
    """
    def __init__(self) -> None:
        self.root = VFSNode("/", "dir")

    def split_path(self, path: str) -> List[str]:
        p = path.strip()
        if not p.startswith("/"):
            raise ValueError("VFS: path must be absolute")
        parts = [part for part in p.split('/') if part]
        return parts

    def add_node(self, path: str, node_type: str, content_b64: str | None = None, overwrite: bool = False) -> None:
        """
        Добавляет узел в VFS по абсолютному пути.
        Если overwrite==False и в целевой ячейке уже есть узел другого типа, метод вернёт ошибку ValueError.
        Если overwrite==True — существующий узел будет перезаписан.
        Промежуточные сегменты обязаны быть директориями; если это не так и overwrite==False - метод вернет ошибку.
        При overwrite==True промежуточный узел будет конвертирован в директорию (и его дети/контент будут очищены).
        """
        if node_type not in {"dir", "file", "link"}:
            print(node_type)
            raise ValueError(f"invalid node_type: {node_type!r}")

        parts = self.split_path(path)
        # Если путь корень:
        if not parts:
            if node_type != "dir":
                raise ValueError("cannot create non-dir at root")
            return

        cur = self.root
        for idx, part in enumerate(parts[:-1]):
            child = cur.children.get(part)
            if child is None:
                # создаём промежуточную директорию
                child = VFSNode(part, "dir")
                cur.children[part] = child
            else:
                # если существующий узел не директория, конфликт
                if child.type != "dir":
                    if not overwrite:
                        raise ValueError(f"intermediate path conflict at '/{'/'.join(parts[:idx+1])}': existing type={child.type}, expected dir")
                    else:
                        print(f"warning: overwriting intermediate node '/{'/'.join(parts[:idx+1])}': {child.type} -> dir")
                        child.type = "dir"
                        child.children = {}
                        child.content = b""
                        child.link_target = None
            cur = child

        last = parts[-1]
        node = cur.children.get(last)
        if node is None:
            # создаём новый конечный узел
            node = VFSNode(last, node_type)
            # инициализация полей
            if node_type == "dir":
                node.children = {}
                node.content = b""
                node.link_target = None
            elif node_type == "file":
                node.children = {}
                node.content = b""
                node.link_target = None
            elif node_type == "link": 
                node.children = {}
                node.content = b""
                node.link_target = None
            cur.children[last] = node
        else:
            # если существует узел с идентичном типом и названием конфликт
            existing_type = getattr(node, "type", None)
            if existing_type and existing_type != node_type:
                if not overwrite:
                    raise ValueError(f"add_node: conflict at '{path}': existing type={existing_type}, new type={node_type}")
                else:
                    print(f"warning: overwriting VFS node at '{path}': existing type={existing_type} -> new type={node_type}")
                    node.type = node_type
                    node.children = {}
                    node.content = b""
                    node.link_target = None
        # заполнение полей
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
            # для dir не требуется дополнительного заполнения полей


    def load_from_csv(self, csv_path: str) -> None:
        """Загрузка VFS из CSV (ожидаются колонки path,type,content_base64)."""
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
                path = row['path']
                node_type = row['type']
                content_b64 = row.get('content_base64') or None
                self.add_node(path, node_type, content_b64, overwrite=True)

    def resolve_node(self, path: str, visited: set | None = None) -> VFSNode:
        """Находит узел по абсолютному пути, разворачивая ссылки."""
        if visited is None:
            visited = set()
        if path == "/":
            return self.root
        parts = self.split_path(path)
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
                # нормализуем относительную ссылку относительно родителя
                if not target.startswith('/'):
                    if processed_parts[:-1]:
                        parent = '/' + '/'.join(processed_parts[:-1]) 
                    else:
                        parent = '/'
                    if parent.endswith('/'):
                        target = parent + target
                    else:
                        target = parent + '/' + target
                if target in visited:
                    raise RecursionError(f"symlink loop detected at {target}")
                visited.add(target)
                cur = self.resolve_node(target, visited=visited)
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

    def debug_tree(self, print_fn=print) -> None:
        """Печатает структуру VFS (дерево) для отладки."""
        def repr_node(node: VFSNode) -> str:
            if node.is_dir():
                return f"{node.name}/"
            elif node.is_file():
                return f"{node.name} (file, {len(node.content)} bytes)"
            else:
                return f"{node.name} -> {node.link_target or '<broken>'}"

        def walk(node: VFSNode, prefix: str) -> None:
            # печатаем текущий узел (для корня отображаем '/')
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

    def __init__(self) -> None:
        if not self.name:
            raise ValueError("Command subclass must set 'name' attribute")
        self.repl: 'REPL' | None = None

    def parse(self, argv: ArgList) -> Tuple[PositionArgsList, OptionsList]:
        options: OptionsList = []
        arguments: PositionArgsList = []
        for token in argv:
            if token == "-":
                arguments.append(token)
            elif token.startswith("-"):
                options.append(token)
            else:
                arguments.append(token)

        for opt in options:
            if self.allowed_options and opt not in self.allowed_options:
                raise InvalidOptionError(f"{self.name}: invalid option: {opt}")

        if self.max_args is not None and len(arguments) > self.max_args:
            raise InvalidArgumentsError(
                f"{self.name}: too many arguments (max {self.max_args})"
            )
        if len(arguments) < self.min_args:
            raise InvalidArgumentsError(
                f"{self.name}: too few arguments (min {self.min_args})"
            )

        return arguments, options

    def execute(self, argv: ArgList) -> None:
        raise NotImplementedError("Command.execute must be implemented")


class LsCommand(Command):
    name = "ls"
    allowed_options: Dict[OptionName, OptionTakesValue] = {
        "-a": False, "-l": False, "-h": False, "-R": False, "-t": False, "-r": False
    }
    min_args = 0
    max_args = None

    def execute(self, argv: ArgList) -> None:
        positional, options = self.parse(argv)
        print("ls called")
        if options:
            print(" options:", " ".join(options))
        else:
            print(" no options")
        if positional:
            print(" args:", " ".join(positional))
        else:
            print(" no args")


class CdCommand(Command):
    name = "cd"
    allowed_options: Dict[OptionName, OptionTakesValue] = {}
    min_args = 0
    max_args = 1

    def execute(self, argv: ArgList) -> None:
        positional, options = self.parse(argv)
        print("cd called")
        if positional:
            print(" arg:", positional[0])
        else:
            print("  no args")
        if options:
            print(" options:", " ".join(options))
        else:
            print(" no options")


class ExitCommand(Command):
    name = "exit"
    allowed_options: Dict[OptionName, OptionTakesValue] = {}
    min_args = 0
    max_args = 0

    def execute(self, argv: ArgList) -> None:
        self.parse(argv)
        print("exit called — exiting")
        sys.exit(0)


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
        # даём доступ командам к vfs (в будущем пригодится)
        for cmd in self.registry.commands.values():
            cmd.repl = self
        # если vfs путь есть, то монтируем его (ожидаем CSV)
        if vfs_path:
            try:
                vfs = VirtualFileSystem()
                vfs.load_from_csv(vfs_path)
                self.vfs = vfs
                self.vfs_cwd = "/"
                print(f"VFS успешно смонтирован из {vfs_path}")
                print("Структура VFS:")
                self.vfs.debug_tree(print_fn=print)
            except Exception as e:
                print(f"не удалось смонтировать vfs из {vfs_path}: {e}")
                sys.exit(1)

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
                    tokens = shlex.split(line, posix=True)
                except Exception as e:
                    raise CommandError(f"parse error: {e}")

                expanded = []
                for t in tokens:
                    try:
                        expanded.append(expand_token(t))
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

    def run_script(self, path: str) -> None:
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

            try:
                try:
                    tokens = shlex.split(line, posix=True)
                except Exception as e:
                    print(f"error in script {path} at line {lineno}: parse error: {e}")
                    sys.exit(1)

                expanded = []
                try:
                    for t in tokens:
                        expanded.append(expand_token(t))
                except EnvironmentVariableNotFoundError as ev:
                    print(f"error in script {path} at line {lineno}: {ev}")
                    sys.exit(1)

                if not expanded:
                    continue

                command_name, *args = expanded
                command = self.registry.get(command_name)
                if command is None:
                    print(f"error in script {path} at line {lineno}: command not found: {command_name}")
                    sys.exit(1)

                try:
                    command.execute(args)
                except CommandError as ce:
                    print(f"error in script {path} at line {lineno}: {ce}")
                    sys.exit(1)
                except SystemExit:
                    raise
                except Exception as e:
                    print(f"unexpected error in script {path} at line {lineno}: {e}")
                    sys.exit(1)

            except SystemExit:
                raise


def make_default_registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(LsCommand())
    registry.register(CdCommand())
    registry.register(ExitCommand())
    return registry

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vfs", "-v", dest="vfs_path", default=None, help="Path to VFS CSV (mount in-memory)")
    parser.add_argument("--script", "-s", dest="script", default=None, help="Path to startup script")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # отладочный вывод параметров
    print("Starting emulator with parameters:")
    print(f" argv: {sys.argv}")
    print(f" vfs_path: {args.vfs_path}")
    print(f" script: {args.script}")
    print(f" process cwd: {os.getcwd()}")

    command_registry = make_default_registry()
    repl = REPL(command_registry, vfs_path=args.vfs_path)

    if args.script:
        try:
            repl.run_script(args.script)
        except FileNotFoundError:
            print(f"start script not found: {args.script}")
            sys.exit(1)
        except SystemExit:
            raise
        except Exception as e:
            print(f"error while executing start script: {e}")
            sys.exit(1)
        else:
            print(f"start script {args.script} finished successfully")
            sys.exit(0)

    repl.run_interactive()


if __name__ == "__main__":
    main()