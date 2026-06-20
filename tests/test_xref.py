import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import xref
from index import _DDL


def _make_index(archive_root: Path) -> sqlite3.Connection:
    """Build a synthetic .cache/index.sqlite with the real schema."""
    cache = archive_root / '.cache'
    cache.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(cache / 'index.sqlite'))
    conn.executescript(_DDL)
    conn.row_factory = sqlite3.Row
    return conn


def _insert_claim(conn, cid, source_id, ctype, value, *, date_edtf=None,
                   place_text=None, status='accepted', persons=()):
    conn.execute(
        '''INSERT INTO claims(id, source_id, type, date_edtf, place_text, value, status)
           VALUES (?,?,?,?,?,?,?)''',
        (cid, source_id, ctype, date_edtf, place_text, value, status),
    )
    for pos, pid in enumerate(persons):
        conn.execute(
            'INSERT INTO claim_persons(claim_id, person_id, position, role) VALUES (?,?,?,?)',
            (cid, pid, pos, None),
        )


class XrefTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self._tmp.name)
        self.conn = _make_index(self.archive_root)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def _seed_persons_sources(self):
        self.conn.execute("INSERT INTO persons(id, name, living, tier, path) VALUES "
                           "('p-aaaaaaaaaa','Test Person','false','curated','x.md')")
        self.conn.execute("INSERT INTO sources(id, title, path) VALUES "
                           "('s-1111111111','Source One','a.md')")
        self.conn.execute("INSERT INTO sources(id, title, path) VALUES "
                           "('s-2222222222','Source Two','b.md')")

    def test_overlapping_dates_corroborate(self) -> None:
        self._seed_persons_sources()
        _insert_claim(self.conn, 'c-aaaaaaaaaa', 's-1111111111', 'birth',
                       'born about 1840', date_edtf='1840~', persons=['p-aaaaaaaaaa'])
        _insert_claim(self.conn, 'c-bbbbbbbbbb', 's-2222222222', 'birth',
                       'born 1840-03-02', date_edtf='1840-03-02', persons=['p-aaaaaaaaaa'])
        self.conn.commit()

        result = xref.run_xref(self.archive_root)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['groups']), 1)
        pairs = result['groups'][0]['pairs']
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]['kind'], 'corroborates')

    def test_non_overlapping_dates_contradict(self) -> None:
        self._seed_persons_sources()
        _insert_claim(self.conn, 'c-aaaaaaaaaa', 's-1111111111', 'birth',
                       'born 1840', date_edtf='1840', persons=['p-aaaaaaaaaa'])
        _insert_claim(self.conn, 'c-bbbbbbbbbb', 's-2222222222', 'birth',
                       'born 1900', date_edtf='1900', persons=['p-aaaaaaaaaa'])
        self.conn.commit()

        result = xref.run_xref(self.archive_root)
        pairs = result['groups'][0]['pairs']
        self.assertEqual(pairs[0]['kind'], 'contradicts')

    def test_overlapping_vital_dates_but_different_place_contradicts(self) -> None:
        self._seed_persons_sources()
        _insert_claim(self.conn, 'c-aaaaaaaaaa', 's-1111111111', 'birth',
                       'born 1840 in New York', date_edtf='1840~',
                       place_text='New York', persons=['p-aaaaaaaaaa'])
        _insert_claim(self.conn, 'c-bbbbbbbbbb', 's-2222222222', 'birth',
                       'born 1840 in Ohio', date_edtf='1840~',
                       place_text='Ohio', persons=['p-aaaaaaaaaa'])
        self.conn.commit()

        result = xref.run_xref(self.archive_root)
        pairs = result['groups'][0]['pairs']
        self.assertEqual(pairs[0]['kind'], 'contradicts')

    def test_overlapping_vital_value_places_contradict_without_place_text(self) -> None:
        self._seed_persons_sources()
        _insert_claim(self.conn, 'c-aaaaaaaaaa', 's-1111111111', 'birth',
                       'born in New York', date_edtf='1840~', persons=['p-aaaaaaaaaa'])
        _insert_claim(self.conn, 'c-bbbbbbbbbb', 's-2222222222', 'birth',
                       'born in Ohio', date_edtf='1840~', persons=['p-aaaaaaaaaa'])
        self.conn.commit()

        result = xref.run_xref(self.archive_root)
        pairs = result['groups'][0]['pairs']
        self.assertEqual(pairs[0]['kind'], 'contradicts')

    def test_overlapping_vital_wording_without_place_still_corroborates(self) -> None:
        self._seed_persons_sources()
        _insert_claim(self.conn, 'c-aaaaaaaaaa', 's-1111111111', 'birth',
                       'born about 1840', date_edtf='1840~', persons=['p-aaaaaaaaaa'])
        _insert_claim(self.conn, 'c-bbbbbbbbbb', 's-2222222222', 'birth',
                       'born 1840-03-02', date_edtf='1840-03-02', persons=['p-aaaaaaaaaa'])
        self.conn.commit()

        result = xref.run_xref(self.archive_root)
        pairs = result['groups'][0]['pairs']
        self.assertEqual(pairs[0]['kind'], 'corroborates')

    def test_same_source_pair_excluded(self) -> None:
        self._seed_persons_sources()
        _insert_claim(self.conn, 'c-aaaaaaaaaa', 's-1111111111', 'birth',
                       'born 1840', date_edtf='1840', persons=['p-aaaaaaaaaa'])
        _insert_claim(self.conn, 'c-bbbbbbbbbb', 's-1111111111', 'birth',
                       'born 1840 again', date_edtf='1840', persons=['p-aaaaaaaaaa'])
        self.conn.commit()

        result = xref.run_xref(self.archive_root)
        self.assertEqual(result['groups'], [])

    def test_already_linked_pair_excluded(self) -> None:
        self._seed_persons_sources()
        _insert_claim(self.conn, 'c-aaaaaaaaaa', 's-1111111111', 'birth',
                       'born 1840', date_edtf='1840', persons=['p-aaaaaaaaaa'])
        _insert_claim(self.conn, 'c-bbbbbbbbbb', 's-2222222222', 'birth',
                       'born 1840 also', date_edtf='1840', persons=['p-aaaaaaaaaa'])
        self.conn.execute(
            "INSERT INTO claim_links(claim_id, rel, target_id) VALUES ('c-aaaaaaaaaa','corroborates','c-bbbbbbbbbb')"
        )
        self.conn.commit()

        result = xref.run_xref(self.archive_root)
        self.assertEqual(result['groups'], [])

    def test_different_type_not_paired(self) -> None:
        self._seed_persons_sources()
        _insert_claim(self.conn, 'c-aaaaaaaaaa', 's-1111111111', 'birth',
                       'born 1840', date_edtf='1840', persons=['p-aaaaaaaaaa'])
        _insert_claim(self.conn, 'c-bbbbbbbbbb', 's-2222222222', 'occupation',
                       'worked as a clerk', date_edtf='1840', persons=['p-aaaaaaaaaa'])
        self.conn.commit()

        result = xref.run_xref(self.archive_root)
        self.assertEqual(result['groups'], [])

    def test_absent_index_returns_failed_status(self) -> None:
        self.conn.close()
        empty_root = Path(tempfile.mkdtemp())
        try:
            result = xref.run_xref(empty_root)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['groups'], [])
        finally:
            import shutil
            shutil.rmtree(empty_root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
