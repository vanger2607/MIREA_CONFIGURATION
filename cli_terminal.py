import os
import re
import socket
import getpass
import shlex
import sys
import argparse
from typing import Dict, List, Tuple, Optional

# Алиасы:
ArgList = List[str]
PositionArgsList = List[str]
OptionsList = List[str]
OptionName = str
OptionTakesValue = bool
IntOrNone = int | None
StringOrNone = Optional[str]


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


def get_user_info(vfs_path: StringOrNone = None) -> str:
    """
    Создаёт приглашение.
    - Если vfs_path задан — показываем только путь VFS.
    - Иначе показываем обычный user@host:cwd.
    """
    user = getpass.getuser()
    host = socket.gethostname()
    if vfs_path:
        return f"{user}@{host}:{vfs_path}$ "
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


class Command:
    name: str = ""
    allowed_options: Dict[OptionName, OptionTakesValue] = {}
    min_args: int = 0
    max_args: IntOrNone = None

    def __init__(self) -> None:
        if not self.name:
            raise ValueError("Command subclass must set 'name' attribute")

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

    def run_interactive(self) -> None:
        while True:
            prompt = get_user_info(self.vfs_path)
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
            prompt = get_user_info(self.vfs_path)
            print(prompt + line)

            if not line.strip() or line.lstrip().startswith("#"):
                continue

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
    parser.add_argument("--vfs", "-v", dest="vfs_path", default=None, help="Path to physical VFS location (display only)")
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
