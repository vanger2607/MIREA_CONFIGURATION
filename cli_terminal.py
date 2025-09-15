#!/usr/bin/env python3
import os
import re
import socket
import getpass
import shlex
import sys
import argparse
from typing import Dict, List, Tuple

# Алиасы:
ArgList = List[str]
PositionArgsList = List[str]
OptionsList = List[str]
OptionName = str
OptionTakesValue = bool
IntOrNone = int | None
StringOrNone = str | None

# -----------------------
# Исключения
# -----------------------
class CommandError(Exception):
    """Базовое исключение для ошибок команд"""
    pass


class EnvironmentVariableNotFoundError(CommandError):
    """Переменная окружения не найдена"""
    def __init__(self, var: str):
        super().__init__(f"environment variable not found: {var}")
        self.var = var


class InvalidArgumentsError(CommandError):
    """Неверное количество или формат аргументов"""
    pass


class InvalidOptionError(CommandError):
    """Неизвестная опция"""
    pass


class ExecutionError(CommandError):
    """Ошибка выполнения команды (runtime)"""
    pass


# -----------------------
# Вспомогательные функции
# -----------------------
def get_user_info(vfs_path: StringOrNone = None) -> str:
    """
    Создаёт приглашение.
    Если vfs_path задан — показываем его (важно для конфигурации этапа 2).
    Иначе показываем обычный user@host:cwd$
    """
    user = getpass.getuser()
    host = socket.gethostname()
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd_display = cwd[len(home) :]
    else:
        cwd_display = cwd

    if vfs_path:
        # отображаем VFS в приглашении, как просили: vfs_path:cwd$
        return f"{user}@{host}:[vfs:{vfs_path}]{cwd_display}$ "
    else:
        return f"{user}@{host}:{cwd_display}$ "


def expand_token(token: str) -> str:
    """
    Раскрываем ~ (expanduser), затем проверяем все вхождения переменных окружения
    ($VAR или ${VAR}). Если какая-то переменная не определена — поднимаем
    EnvironmentVariableNotFoundError. Если все OK — возвращаем результат expandvars.
    """
    expanded = os.path.expanduser(token)

    # Pattern находит $VAR и ${VAR}
    pattern = re.compile(r"\$(\w+)|\$\{([^}]+)\}")
    matches = pattern.findall(expanded)

    for m in matches:
        var = m[0] or m[1]  # либо $VAR, либо ${VAR}
        if var not in os.environ:
            raise EnvironmentVariableNotFoundError(var)

    return os.path.expandvars(expanded)


# -----------------------
# Базовый класс команд
# -----------------------
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
        """Простейший парсер опций/позиционных аргументов"""
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
        """Заглушка"""
        raise NotImplementedError("Command.execute must be implemented")


# -----------------------
# Команды
# -----------------------
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
    allowed_options: Dict[OptionName, OptionTakesValue] = {"..": False, "~": False, "/": False}
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


# -----------------------
# Реестр команд
# -----------------------
class CommandRegistry:
    def __init__(self) -> None:
        self.commands: Dict[str, Command] = {}

    def register(self, command: Command) -> None:
        self.commands[command.name] = command

    def get(self, name: str) -> Command | None:
        return self.commands.get(name)

    def names(self) -> List[str]:
        return sorted(self.commands.keys())


# -----------------------
# REPL
# -----------------------
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
                print()  # newline on Ctrl-D
                return

            if not line.strip():
                continue

            try:
                try:
                    tokens = shlex.split(line, posix=True)
                except Exception as e:
                    raise CommandError(f"parse error: {e}")

                # expand tokens, catch missing env var explicitly
                expanded = []
                for t in tokens:
                    try:
                        expanded.append(expand_token(t))
                    except EnvironmentVariableNotFoundError as ev:
                        # показываем читаемое сообщение и не выполняем команду
                        print("error:", ev)
                        expanded = None
                        break
                if expanded is None:
                    continue  # вернуться в интерактив

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
        """
        Выполнить стартап-скрипт.
         - печатает приглашение + ввод (строка из скрипта)
         - выполняет команду; при первой ошибке сообщает с номером строки и завершает с кодом 1
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
            line = raw.rstrip("\n")
            # пропускаем пустые строки и комментарии
            if not line.strip() or line.lstrip().startswith("#"):
                continue

            prompt = get_user_info(self.vfs_path)
            # эхо ввода: приглашение + команда
            print(prompt + line)

            # разбираем и выполняем; при любой ошибке — сообщаем и выходим (stop on first error)
            try:
                try:
                    tokens = shlex.split(line, posix=True)
                except Exception as e:
                    print(f"error in script {path} at line {lineno}: parse error: {e}")
                    sys.exit(1)

                # раскрытие переменных — отдельная обработка, чтобы корректно показать ошибку
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
                    # если команда внутри вызвала exit() — распространяем
                    raise
                except Exception as e:
                    print(f"unexpected error in script {path} at line {lineno}: {e}")
                    sys.exit(1)

            except SystemExit:
                # позволяем exit() завершить всё приложение
                raise


# -----------------------
# Утилиты
# -----------------------
def make_default_registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(LsCommand())
    registry.register(CdCommand())
    registry.register(ExitCommand())
    return registry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configurable emulator (stage 2)")
    parser.add_argument("--vfs", "-v", dest="vfs_path", default=None, help="Path to physical VFS location")
    parser.add_argument("--script", "-s", dest="script", default=None, help="Path to startup script")
    return parser.parse_args()


# -----------------------
# main
# -----------------------
def main() -> None:
    args = parse_args()

    # отладочный вывод всех параметров при старте
    print("Starting emulator with parameters:")
    print(f" argv: {sys.argv}")
    print(f" vfs_path: {args.vfs_path}")
    print(f" script: {args.script}")

    if args.vfs_path:
        try:
            expanded_vfs = expand_token(args.vfs_path)
            print(f" expanded vfs_path: {expanded_vfs}")
        except EnvironmentVariableNotFoundError as ev:
            print("error:", ev)
            sys.exit(1)

    command_registry = make_default_registry()
    repl = REPL(command_registry, vfs_path=args.vfs_path)

    if args.script:
        try:
            repl.run_script(args.script)
        except FileNotFoundError:
            print(f"start script not found: {args.script}")
            sys.exit(1)
        except SystemExit:
            # если внутри скрипта вызван exit(), завершаем приложение
            raise
        except Exception as e:
            print(f"error while executing start script: {e}")
            sys.exit(1)
        else:
            print(f"start script {args.script} finished successfully")

    # перейти в интерактивный режим
    repl.run_interactive()


if __name__ == "__main__":
    main()

