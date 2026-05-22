import base64
import unittest

from gateway_py3.folder_dialog import _encoded_script, _folder_dialog_script, _powershell_command


class FolderDialogTests(unittest.TestCase):
    def test_folder_dialog_script_uses_windows_folder_picker(self):
        script = _folder_dialog_script("选择 '项目' 目录")

        self.assertIn("System.Windows.Forms", script)
        self.assertIn("FolderBrowserDialog", script)
        self.assertIn("$owner.TopMost = $true", script)
        self.assertIn("$dialog.ShowDialog($owner)", script)
        self.assertIn("选择 ''项目'' 目录", script)

    def test_powershell_command_is_encoded_for_safe_arguments(self):
        command = _powershell_command("选择项目目录")
        encoded = _encoded_script("选择项目目录")

        self.assertEqual(command[:6], ["powershell.exe", "-NoLogo", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass"])
        self.assertEqual(command[-2], "-EncodedCommand")
        self.assertEqual(command[-1], encoded)
        decoded = base64.b64decode(encoded).decode("utf-16le")
        self.assertIn("FolderBrowserDialog", decoded)


if __name__ == "__main__":
    unittest.main()
