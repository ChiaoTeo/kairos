from io import StringIO
import unittest

from kairospy.cli_progress import TerminalProgressMatrix


class _TtyBuffer(StringIO):
    def isatty(self):
        return True


class TerminalProgressMatrixTests(unittest.TestCase):
    def test_reusable_matrix_redraws_in_place_on_a_tty(self):
        stream = _TtyBuffer()
        matrix = TerminalProgressMatrix("Download", ("2025",), ("01", "02"), stream=stream)
        matrix.set_cell("2025", "01", "0/10")
        matrix.set_cell("2025", "02", "-")
        matrix.set_footer("Status: planned")
        matrix.render(force=True)
        first_lines = len(matrix.lines())
        matrix.set_cell("2025", "01", "5/10")
        matrix.set_footer("Status: downloading")
        matrix.render(force=True)
        output = stream.getvalue()
        self.assertIn("Download", output)
        self.assertIn("5/10", output)
        self.assertIn(f"\x1b[{first_lines}F", output)
        self.assertIn("\x1b[2K", output)

    def test_non_tty_matrix_produces_portable_snapshot_without_ansi(self):
        stream = StringIO()
        matrix = TerminalProgressMatrix("Plan", ("2025",), ("01",), stream=stream)
        matrix.set_cell("2025", "01", "1/1")
        matrix.render(force=True)
        self.assertIn("1/1", stream.getvalue())
        self.assertNotIn("\x1b[", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
