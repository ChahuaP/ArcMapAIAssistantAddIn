import unittest

from gateway_py3.app import _is_poll_access_message


class HttpAccessLoggingTests(unittest.TestCase):
    def test_polling_gets_are_not_written_to_access_log(self):
        self.assertTrue(_is_poll_access_message('"GET /projects HTTP/1.1" 200 -'))
        self.assertTrue(_is_poll_access_message('"GET /context HTTP/1.1" 200 -'))
        self.assertTrue(_is_poll_access_message('"GET /api/workflows HTTP/1.1" 200 -'))

    def test_non_polling_access_is_still_loggable(self):
        self.assertFalse(_is_poll_access_message('"POST /plan HTTP/1.1" 200 -'))
        self.assertFalse(_is_poll_access_message('"GET /api/workflows HTTP/1.1" 500 -'))


if __name__ == "__main__":
    unittest.main()
