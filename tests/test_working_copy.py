import builtins
import contextlib
import io
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import fha
import packet
import photoindex
import process
import working_copy
from _lib import EXIT_CLEAN


def _copy_fixture(tmp: Path) -> Path:
    """Copy the working-copy fixture so tests can mutate marker/cache files."""
    src = ROOT / 'tests' / 'fixtures' / 'working-copy'
    dst = tmp / 'working-copy'
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns('.cache'))
    return dst


def _copy_photo_fixture(tmp: Path) -> Path:
    src = ROOT / 'tests' / 'fixtures' / 'photo-fixture'
    dst = tmp / 'photo-fixture'
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns('.cache'))
    return dst


class WorkingCopyTests(unittest.TestCase):
    def test_status_accepts_parent_and_child_root(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            archive = _copy_fixture(Path(d))
            for argv in (
                ['working-copy', '--root', str(archive), 'status'],
                ['working-copy', 'status', '--root', str(archive)],
            ):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(fha.main(argv), EXIT_CLEAN)
                self.assertIn('Working-copy mode: ON', out.getvalue())

    def test_bare_working_copy_reports_status(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            archive = _copy_fixture(Path(d))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(fha.main(['working-copy', '--root', str(archive)]), EXIT_CLEAN)
            self.assertIn('Working-copy mode: ON', out.getvalue())

    def test_off_prompt_names_unreachable_asset_roots_and_decline_keeps_marker(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            archive = _copy_fixture(Path(d))
            prompts: list[str] = []
            orig_input = builtins.input
            builtins.input = lambda prompt='': (prompts.append(prompt) or 'n')
            try:
                code = working_copy._cmd_off(type('Args', (), {'root': str(archive), 'yes': False})())
            finally:
                builtins.input = orig_input

            self.assertEqual(code, EXIT_CLEAN)
            self.assertTrue((archive / 'WORKING_COPY').exists())
            prompt = ''.join(prompts)
            self.assertIn('photos root', prompt)
            self.assertIn('documents root', prompt)
            self.assertIn('not reachable', prompt)

    def test_result_messages_are_json_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            archive = _copy_fixture(Path(d))
            (archive / 'WORKING_COPY').unlink()

            orig = working_copy._ensure_gitignore_entry
            working_copy._ensure_gitignore_entry = lambda root: (_ for _ in ()).throw(
                OSError('locked')
            )
            try:
                result = working_copy.run_working_copy_on(archive)
            finally:
                working_copy._ensure_gitignore_entry = orig

            payload = result.as_dict()
            self.assertEqual(payload['messages'][0]['level'], 'warning')
            self.assertTrue((archive / 'WORKING_COPY').is_file())

    def test_reconcile_refuses_without_mutating_cache(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            archive = _copy_photo_fixture(Path(d))
            cfg = {'roots': {'photos': 'photos'}}
            orig_run_exiftool = photoindex._run_exiftool
            photoindex._run_exiftool = lambda paths: [
                {'SourceFile': str(p), 'Caption-Abstract': p.name} for p in paths
            ]
            try:
                photoindex.run_scan(archive, cfg)
            finally:
                photoindex._run_exiftool = orig_run_exiftool

            (archive / 'WORKING_COPY').write_text('working copy\n', encoding='utf-8')
            shutil.rmtree(archive / 'photos')
            (archive / 'photos').mkdir()

            before = self._photo_paths(archive)
            result = photoindex.run_reconcile(archive, cfg)
            after = self._photo_paths(archive)

            self.assertEqual(result.exit_code, EXIT_CLEAN)
            self.assertTrue(result['working_copy'])
            self.assertEqual(before, after)
            self.assertFalse(any(path.startswith('MISSING:') for path in after))

    def test_asset_refusals_exit_clean(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            archive = _copy_fixture(Path(d))
            self.assertEqual(
                process._run_process(type('Args', (), {'root': str(archive), 'file': 'x.jpg'})()),
                EXIT_CLEAN,
            )
            self.assertEqual(
                packet._cmd_packet(type('Args', (), {'root': str(archive), 'person_id': 'P-wc00000001'})()),
                EXIT_CLEAN,
            )
            self.assertEqual(
                photoindex._cmd_tag_person(type('Args', (), {
                    'root': str(archive), 'person_id': 'P-wc00000001',
                    'from_face_tag': None, 'paths': ['photos/x.jpg'], 'dry_run': False,
                })()),
                EXIT_CLEAN,
            )

    @staticmethod
    def _photo_paths(archive: Path) -> list[str]:
        conn = sqlite3.connect(archive / '.cache' / 'photos.sqlite')
        try:
            return [row[0] for row in conn.execute('SELECT path FROM photos ORDER BY path')]
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
