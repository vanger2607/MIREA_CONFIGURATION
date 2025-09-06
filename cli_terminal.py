import os
import socket
import getpass
import shlex
import sys

ls_options = ["-a", "-l", "-h", "-R", "-t", "-r", "-1"]
cd_options = ["-", "~"]


def get_user_info():
    """Получение данных о пользователе"""
    user = getpass.getuser()  # возвращает никнейм текущего пользователя
    host = socket.gethostname()  # возвращает имя хоста компьютера
    cwd = os.getcwd()  # return current working directory
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home) :]
    return f"{user}@{host}:{cwd}$ "


def expand_env(token: str) -> str:
    """Раскрытие переменных окружения."""
    return os.path.expandvars(token)


def repl():
    while True:
        try:
            info = get_user_info()
            print(info)
            line = input()
        except EOFError:
            print("EXIT!!!")
            break
        if not line.strip():
            continue

        try:
            tokens = shlex.split(line, posix=True)
        except Exception as e:
            print(f"error: {e}")
            continue

        if not tokens:
            continue

        args = []
        for t in tokens:
            if t.startswith("~"):
                t = os.path.expanduser(t)
            t = expand_env(t)
            args.append(t)

        cmd = args[0]

        if cmd == "exit":
            if len(args) > 1:
                print("error: exit: too many arguments")
                continue
            sys.exit(0)

        elif cmd == "ls":
            print("ls called with arguments:", " ".join(args[1:]))

            print(
                "supported ls options:", " ".join(ls_options)
            )  # повторяющийся код, возможно стоит вынести в функцию

        elif cmd == "cd":
            if len(args) > 2:
                print("error: cd: too many arguments")
            else:
                print("cd called with argument:", args[1:])
            print("supported cd options:", " ".join(cd_options))

        else:
            print(f"error: command not found: {cmd}")


if __name__ == "__main__":
    repl()
