import unittest
import io
import base64
from contextlib import redirect_stdout
from unittest.mock import patch


import cli_terminal as emulator


def b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


class BaseVFSCommandTest(unittest.TestCase):
    def setUp(self):
        # создаём реестр и REPL; REPL установит .repl у команд
        registry = emulator.make_default_registry()
        self.repl = emulator.REPL(registry, vfs_path=None)
        self.repl.vfs = emulator.VirtualFileSystem()
        self.repl.vfs_cwd = "/"
        self.registry = registry

    def run_cmd_capture(self, name, argv):
        """Выполнить команду по имени и вернуть stdout как строку."""
        cmd = self.registry.get(name)
        f = io.StringIO()
        with redirect_stdout(f):
            cmd.execute(argv)
        return f.getvalue()


class TestMkdir(BaseVFSCommandTest):
    def test_mkdir_basic_creates_directory(self):
        out = self.run_cmd_capture("mkdir", ["newdir"])
        # каталог должен появиться
        self.assertTrue(self.repl.vfs.exists("/newdir"))
        # по умолчанию без -v — вывода нет
        self.assertEqual(out, "")

    def test_mkdir_verbose_prints_message(self):
        out = self.run_cmd_capture("mkdir", ["-v", "foo"])
        self.assertTrue(self.repl.vfs.exists("/foo"))
        self.assertIn("mkdir: created directory '/foo'", out)

    def test_mkdir_parents_and_verbose_behavior(self):
        # note: тестирует текущее поведение реализованного mkdir
        # при -p mkdir создаёт промежуточные директории внутри add_node, но печатает только итоговый путь (последний).
        out = self.run_cmd_capture("mkdir", ["-p", "-v", "/a/b/c"])
        self.assertTrue(self.repl.vfs.exists("/a"))
        self.assertTrue(self.repl.vfs.exists("/a/b"))
        self.assertTrue(self.repl.vfs.exists("/a/b/c"))
        # печатается только один message для самого abs_path
        self.assertIn("mkdir: created directory '/a/b/c'", out)
        # не ожидаем сообщений для промежуточных уровней
        self.assertNotIn("mkdir: created directory '/a'", out)
        self.assertNotIn("mkdir: created directory '/a/b'", out)


class TestRm(BaseVFSCommandTest):
    def test_rm_file(self):
        # создаём файл /f
        self.repl.vfs.add_node("/f", "file", b64("hello"), overwrite=False)
        out = self.run_cmd_capture("rm", ["/f"])
        self.assertFalse(self.repl.vfs.exists("/f"))
        self.assertEqual(out, "")  # без -v/интерактивных флагов — вывода нет

    def test_rm_dir_non_recursive_fails(self):
        # создаём непустую директорию /d/x
        self.repl.vfs.add_node("/d/x", "file", b64("x"), overwrite=False)
        out = self.run_cmd_capture("rm", ["/d"])
        self.assertTrue(self.repl.vfs.exists("/d"))
        self.assertIn("Is a directory (use -r to remove recursively)", out)

    def test_rm_dir_recursive_with_prompt(self):
        self.repl.vfs.add_node("/dir/sub", "file", b64("x"), overwrite=False)
        # имитируем ввод 'y' на prompt
        with patch("builtins.input", return_value="y"):
            out = self.run_cmd_capture("rm", ["-r", "-i", "/dir"])
        self.assertFalse(self.repl.vfs.exists("/dir"))
        # rm при успешном удалении не печатает ничего (в текущей реализации) — допустимо пусто
        self.assertEqual(out, "")

    def test_cannot_remove_root_via_vfs_api(self):
        # прямой вызов VFS.remove_node должен выбросить ValueError для "/"
        with self.assertRaises(ValueError):
            self.repl.vfs.remove_node("/", recursive=True)

    def test_rm_root_via_command_prints_error(self):
        # вызов команды rm -r / должен попытаться удалить и напечатать ошибку
        out = self.run_cmd_capture("rm", ["-r", "/"])
        self.assertIn("rm: error removing '/'", out)


class TestLsCdPwd(BaseVFSCommandTest):
    def setUp(self):
        super().setUp()
        # создаём структуру /a, /a/.hidden, /a/file1
        self.repl.vfs.add_node("/a", "dir", None, overwrite=False)
        self.repl.vfs.add_node("/a/.hidden", "file", b64("hidden"), overwrite=False)
        self.repl.vfs.add_node("/a/file1", "file", b64("data"), overwrite=False)

    def test_ls_basic(self):
        out = self.run_cmd_capture("ls", ["/a"])
        # без -a скрытые не показываем, ожидаем file1
        self.assertIn("file1", out)
        self.assertNotIn(".hidden", out)

    def test_ls_all_and_long(self):
        out = self.run_cmd_capture("ls", ["-a", "-l", "/a"])
        # оба файла должны присутствовать и быть показаны в длинном формате (type + size + name)
        self.assertIn(".hidden", out)
        self.assertIn("file1", out)
        self.assertRegex(out, r"file\s+\d+\s+file1")

    def test_cd_and_pwd(self):
        # смена директории на /a
        out_cd = self.run_cmd_capture("cd", ["/a"])
        # cd ничего не печатает при успехе
        self.assertEqual(out_cd, "")
        out_pwd = self.run_cmd_capture("pwd", [])
        self.assertEqual(out_pwd.strip(), "/a")


class TestWcUniq(BaseVFSCommandTest):
    def test_wc_counts(self):
        content = "one two\nthree\n"
        self.repl.vfs.add_node("/file", "file", b64(content), overwrite=False)
        out = self.run_cmd_capture("wc", ["/file"])
        # ожидания: строки=2, слова=3, байты = len(content)
        self.assertIn(str(len(content.encode("utf-8"))), out)
        self.assertIn("2", out)  # lines
        self.assertIn("3", out)  # words

    def test_uniq_basic_and_counts(self):
        # подготовим файл с повторяющимися строками
        lines = ["a", "a", "b", "b", "b", "c", "a"]
        content = "\n".join(lines) + "\n"
        self.repl.vfs.add_node("/u", "file", b64(content), overwrite=False)
        out = self.run_cmd_capture("uniq", ["-A", "-c", "/u"])
        # ожидаем, что 'a' встретится 3 раза (две подряд + один в конце учитывается глобально -> 3)
        self.assertIn("3", out)
        self.assertIn("a", out)


class TestHelp(BaseVFSCommandTest):
    def test_help_lists_commands(self):
        out = self.run_cmd_capture("help", [])
        self.assertIn("Available commands:", out)
        # проверим, что все заранее зарегистрированные команды отображаются
        for name in ["ls", "cd", "exit", "wc", "uniq", "pwd", "rm", "mkdir", "help"]:
            self.assertIn(name, out)


if __name__ == "__main__":
    unittest.main()
