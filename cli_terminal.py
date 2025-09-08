import os
import socket
import getpass
import shlex
import sys

from typing import Dict, List, Tuple

# Алиасы:
ArgList = List[str]
PositionArgsList = List[str]
OptionsList = List[str]
OptionName = str
OptionTakesValue = bool
IntOrNone = int | None


# Исключения:
class CommandError(Exception):
    """Базовое исключение для ошибок команд"""

    pass


class InvalidArgumentsError(CommandError):
    """Неверное количество или формат аргументов"""

    pass


class InvalidOptionError(CommandError):
    """Неизвестная опция"""

    pass


class ExecutionError(CommandError):
    """Ошибка выполнения команды (runtime)"""

    pass


# вспомогательные функции:
def get_user_info() -> str:
    """Создаёт приглашение вида user@host:cwd$ (cwd сокращается к ~ при необходимости)"""
    user = getpass.getuser()
    host = socket.gethostname()
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd_display = "~" + cwd[len(home) :]
    else:
        cwd_display = cwd
    return f"{user}@{host}:{cwd_display}$ "


def expand_token(token: str) -> str:
    """Сначала раскрываем ~ (expanduser), затем переменные окружения $VAR (expandvars)"""
    return os.path.expandvars(os.path.expanduser(token))


# Базовый класс команд
class Command:
    """Базовый класс для команд"""

    name: str = ""
    allowed_options: Dict[OptionName, OptionTakesValue] = {}
    min_args: int = 0
    max_args: IntOrNone = None

    def __init__(self) -> None:
        if not self.name:
            raise ValueError("Command subclass must set 'name' attribute")

    def parse(self, argv: ArgList) -> Tuple[PositionArgsList, OptionsList]:
        """Базовый парсер, не поддерживающий большое количество функционала"""
        options: OptionsList = []
        arguments: PositionArgsList = []
        for token in argv:
            if token == "-":
                arguments.append(token)
            elif token.startswith("-"):
                options.append(token)
            else:
                arguments.append(token)

        # блок проверок, возможно стоит вынести в отдельные функции
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
        """Заглушка, метод обязательно переписывать в классах-наследниках"""
        raise NotImplementedError("Command.execute must be implemented")


# Команды:
class LsCommand(Command):
    name = "ls"
    allowed_options: Dict[OptionName, OptionTakesValue] = {
        "-a": False,
        "-l": False,
        "-h": False,
        "-R": False,
        "-t": False,
        "-r": False,
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
    allowed_options: Dict[OptionName, OptionTakesValue] = {
        "..": False,
        "~": False,
        "/": False,
    }
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


# Реестр команд
class CommandRegistry:
    def __init__(self) -> None:
        self.commands: Dict[str, Command] = {}

    def register(self, command: Command) -> None:
        self.commands[command.name] = command

    def get(self, name: str) -> Command | None:
        return self.commands.get(name)

    def names(self) -> List[str]:
        return sorted(self.commands.keys())


# REPL
class REPL:
    def __init__(self, registry: CommandRegistry) -> None:
        self.registry = registry

    def run_interactive(self) -> None:
        while True:
            prompt = get_user_info()
            print(prompt, end="")
            line = input()
            try:
                if not line.strip():
                    continue
                try:
                    tokens = shlex.split(line, posix=True)
                except Exception as e:
                    raise CommandError(f"parse error: {e}")
                expanded = [expand_token(t) for t in tokens]
                command_name, *args = expanded
                command = self.registry.get(command_name)
                if command is None:
                    raise CommandError(f"command not found: {command_name}")
                command.execute(args)
            except CommandError as ce:
                print("error:", ce)
            except Exception as e:
                print("unexpected error:", e)


def make_default_registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(LsCommand())
    registry.register(CdCommand())
    registry.register(ExitCommand())
    return registry


def main() -> None:
    command_registry = make_default_registry()
    repl = REPL(command_registry)
    repl.run_interactive()


if __name__ == "__main__":
    main()
