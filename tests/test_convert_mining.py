"""Tests for `fha convert-mining` (BUILD.md M7.8 — legacy interview migration).

Copies the tests/fixtures/legacy-export/ input to a throwaway tree, exercises
the dry-run (writes nothing) and `--apply` (mints sources/claims/person stubs,
imports stories + questions, writes the mapping), and asserts the converted
archive lints with no errors — the M7.8 "Done when" contract.

Run: python -m unittest tests.test_convert_mining -v   (from the repo root)
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import convert_mining
import lint
from _lib import EXIT_CLEAN, EXIT_ERRORS, load_fha_yaml, read_record

FIXTURE = ROOT / 'tests' / 'fixtures' / 'legacy-export'


class ConvertMiningTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.archive = Path(self._tmp.name) / 'legacy-export'
        shutil.copytree(FIXTURE, self.archive)
        self.config = load_fha_yaml(self.archive, strict=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _apply(self) -> int:
        return convert_mining.run_convert(self.archive, self.config, apply=True)

    # ── dry-run ──────────────────────────────────────────────────────────────

    def test_dry_run_writes_nothing(self) -> None:
        rc = convert_mining.run_convert(self.archive, self.config, apply=False)
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertFalse((self.archive / 'sources').exists())
        self.assertFalse((self.archive / 'people').exists())
        self.assertFalse((self.archive / '.cache' / 'convert_mapping.csv').exists())

    # ── apply ────────────────────────────────────────────────────────────────

    def test_apply_creates_sources_claims_and_stubs(self) -> None:
        self.assertEqual(self._apply(), EXIT_CLEAN)

        sources = sorted((self.archive / 'sources' / 'interview').glob('*_S-*.md'))
        self.assertEqual(len(sources), 2)
        stubs = sorted((self.archive / 'people' / 'stubs').glob('*_P-*.md'))
        self.assertEqual(len(stubs), 2)
        stub_meta = read_record(stubs[0])['meta']
        self.assertEqual(stub_meta['tier'], 'stub')
        self.assertEqual(stub_meta['living'], 'unknown')

        mary = next(p for p in sources if 'mary' in p.name)
        rec = read_record(mary)
        self.assertEqual(rec['parse_errors'], [])
        self.assertEqual(rec['meta']['source_type'], 'interview')

        # Transcript filed under documents/interviews/ with the S-id, name kept.
        transcripts = list((self.archive / 'documents' / 'interviews').glob('*_S-*.txt'))
        self.assertEqual(len(transcripts), 2)
        self.assertEqual(rec['meta']['files'][0]['original_filename'], 'T001.txt')

        claims = rec['claims']
        by_type = {c['type']: c for c in claims}
        self.assertIn('birth', by_type)
        self.assertIn('marriage', by_type)
        for c in claims:
            self.assertEqual(c['status'], 'suggested')
            self.assertTrue(c['id'].startswith('C-'))
        # Earliest==Latest collapses to a single EDTF value.
        self.assertEqual(by_type['birth']['date'], '1890')
        # Update(T###) line merged into the claim notes.
        self.assertIn('April', by_type['marriage']['notes'])
        # Best-effort anchor resolved to the transcript line.
        self.assertEqual(by_type['birth']['anchor'], 'line 3')
        text = mary.read_text(encoding='utf-8')
        self.assertIn('## AI Passes', text)
        self.assertIn('model: gpt-4-class', text)
        self.assertIn('human_reviewed: false', text)

    def test_apply_imports_stories_and_questions(self) -> None:
        self._apply()
        mary = next((self.archive / 'sources' / 'interview').glob('*mary*_S-*.md'))
        body = mary.read_text(encoding='utf-8')
        self.assertIn('## Stories', body)
        self.assertIn('wagon journey', body)
        self.assertIn('[P-a1a1a1a1a1]', body)            # person resolved to a token

        questions = (self.archive / 'notes' / 'questions.md').read_text(encoding='utf-8')
        self.assertIn('## Q: Where exactly was Mary Hartley born?', questions)
        self.assertIn('origin: tool', questions)
        self.assertIn('S-', questions)                   # source ref mapped to its S-id

    def test_apply_writes_mapping_csv(self) -> None:
        self._apply()
        mapping = (self.archive / '.cache' / 'convert_mapping.csv').read_text(encoding='utf-8')
        self.assertIn('legacy_id,new_id,notes', mapping)
        self.assertIn('S001,S-', mapping)
        self.assertIn('Mary Hartley,P-a1a1a1a1a1', mapping)

    def test_apply_refuses_existing_mapping(self) -> None:
        cache = self.archive / '.cache'
        cache.mkdir()
        (cache / 'convert_mapping.csv').write_text('already converted\n', encoding='utf-8')

        rc = self._apply()
        self.assertEqual(rc, EXIT_ERRORS)
        self.assertFalse((self.archive / 'sources').exists())
        self.assertEqual(
            (cache / 'convert_mapping.csv').read_text(encoding='utf-8'),
            'already converted\n',
        )

    def test_apply_rolls_back_after_write_failure(self) -> None:
        with mock.patch('convert_mining.shutil.copy2', side_effect=OSError('disk full')):
            rc = self._apply()

        self.assertEqual(rc, convert_mining.EXIT_FAILURE)
        self.assertFalse((self.archive / 'people').exists())
        self.assertFalse((self.archive / 'sources').exists())
        self.assertFalse((self.archive / 'documents').exists())
        self.assertFalse((self.archive / '.cache' / 'convert_mapping.csv').exists())

    def test_converted_archive_lints_without_errors(self) -> None:
        self._apply()
        n_errors, _n_warnings, _e018 = lint.run_lint_silent(self.archive, self.config)
        self.assertEqual(n_errors, 0, 'converted archive must lint with no E-level findings')

    # ── derivation units ─────────────────────────────────────────────────────

    def test_type_heuristics(self) -> None:
        self.assertEqual(convert_mining.derive_claim_type('Born in Kansas', 'Vitals')[0], 'birth')
        self.assertEqual(convert_mining.derive_claim_type('Served in the infantry', 'Military')[0], 'military')
        self.assertEqual(convert_mining.derive_claim_type('Worked as a clerk', 'Work')[0], 'occupation')
        # Unmatched → event with the Section as subtype.
        t, sub = convert_mining.derive_claim_type('Won a county fair ribbon', 'Anecdotes')
        self.assertEqual(t, 'event')
        self.assertEqual(sub, 'anecdotes')

    def test_legacy_to_edtf(self) -> None:
        self.assertEqual(convert_mining.legacy_to_edtf('1890', '1890'), '1890')
        self.assertEqual(convert_mining.legacy_to_edtf('1880', '1885'), '1880/1885')
        self.assertEqual(convert_mining.legacy_to_edtf('1890~', '1890'), '1890~')
        self.assertIsNone(convert_mining.legacy_to_edtf('', ''))
        self.assertIsNone(convert_mining.legacy_to_edtf('unknown', ''))

    def test_missing_mining_dir_errors(self) -> None:
        empty = self.archive.parent / 'empty'
        empty.mkdir()
        (empty / 'fha.yaml').write_text('roots: {}\n', encoding='utf-8')
        rc = convert_mining.run_convert(empty, load_fha_yaml(empty, strict=True), apply=False)
        self.assertEqual(rc, EXIT_ERRORS)


if __name__ == '__main__':
    unittest.main()
