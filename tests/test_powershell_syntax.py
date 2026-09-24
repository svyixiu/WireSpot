"""Every embedded PowerShell script must parse under Windows PowerShell 5.1
(exactly as winexec assembles it: param block, prelude, body)."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from wirespot import scripts, winexec

PARSE = r'''
$errs = $null; $tokens = $null
[void][System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$tokens, [ref]$errs)
if ($errs.Count) { $errs | ForEach-Object { "$($_.Extent.StartLineNumber): $($_.Message)" }; exit 1 }
'''


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell required")
class PowerShellSyntaxTests(unittest.TestCase):
    def test_all_scripts_parse(self):
        names = [n for n in dir(scripts) if n.endswith("_PS") and isinstance(getattr(scripts, n), str)]
        self.assertGreater(len(names), 8)
        with tempfile.TemporaryDirectory() as d:
            checker = Path(d) / "check.ps1"
            checker.write_text(PARSE, encoding="utf-8-sig")
            for n in names:
                head, rest = winexec.split_param_block(getattr(scripts, n))
                body = head + winexec.PS_PRELUDE + winexec.PS_WINRT + rest
                f = Path(d) / f"{n}.ps1"
                f.write_text(body, encoding="utf-8-sig")
                p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                    "-File", str(checker), str(f)], capture_output=True, text=True, timeout=60)
                self.assertEqual(p.returncode, 0, f"{n}: {p.stdout}{p.stderr}")


if __name__ == "__main__":
    unittest.main()
