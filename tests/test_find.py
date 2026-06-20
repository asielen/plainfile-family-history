import argparse
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import find
from _lib import EXIT_CLEAN, EXIT_FAILURE, EXIT_WARNINGS
from index import _DDL


def _make_index(archive_root: Path) -> sqlite3.Connection:
    cache = archive_root / '.cache'
    cache.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(cache / 'index.sqlite'))
    conn.executescript(_DDL)
    conn.row_factory = sqlite3.Row
    return conn


def _add_person(conn, pid, name):
    conn.execute("INSERT INTO persons(id, name, living, tier, path) VALUES (?,?,?,?,?)",
                 (pid, name, 'false', 'curated', f'{pid}.md'))


def _add_source(conn, sid, title, source_type=None, repository=None):
    conn.execute("INSERT INTO sources(id, title, source_type, repository, path) VALUES (?,?,?,?,?)",
                 (sid, title, source_type, repository, f'{sid}.md'))


def _add_claim(conn, cid, sid, ctype, value, persons, *, status='accepted',
                date_edtf=None, date_min=None, date_max=None,
                place_id=None, place_text=None, subtype=None, hypothesis=None):
    conn.execute(
        '''INSERT INTO claims(id, source_id, type, subtype, value, status, date_edtf,
           date_min, date_max, place_id, place_text, hypothesis)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
        (cid, sid, ctype, subtype, value, status, date_edtf, date_min, date_max,
         place_id, place_text, hypothesis),
    )
    for pos, pid in enumerate(persons):
        conn.execute(
            'INSERT INTO claim_persons(claim_id, person_id, position, role) VALUES (?,?,?,?)',
            (cid, pid, pos, None),
        )


def _run(func, *args, **kwargs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = func(*args, **kwargs)
    return rc, buf.getvalue()


class RelatedPersonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self._tmp.name)
        self.conn = _make_index(self.archive_root)
        _add_person(self.conn, 'p-aaaaaaaaaa', 'Alice')
        _add_person(self.conn, 'p-bbbbbbbbbb', 'Bob')
        _add_person(self.conn, 'p-cccccccccc', 'Carol')
        _add_source(self.conn, 's-1111111111', 'Census')
        _add_source(self.conn, 's-2222222222', 'Obituary')

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_relationship_edge_with_source_count(self) -> None:
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'relationship',
                    'Alice is spouse of Bob', ['p-aaaaaaaaaa', 'p-bbbbbbbbbb'])
        self.conn.execute(
            "INSERT INTO relationships(person_id, rel, other_id, claim_id) "
            "VALUES ('p-aaaaaaaaaa','spouse','p-bbbbbbbbbb','c-1111111111')"
        )
        self.conn.commit()

        rc, out = _run(find.run_related, 'p-aaaaaaaaaa', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('spouse: Bob [p-bbbbbbbbbb] — 1 source(s)', out)

    def test_cooccurrence_excludes_existing_relationship(self) -> None:
        # Alice/Bob share two sources and already have a relationship edge —
        # should appear under relationships, not co-occurrence.
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'residence', 'lived together',
                    ['p-aaaaaaaaaa', 'p-bbbbbbbbbb'])
        _add_claim(self.conn, 'c-2222222222', 's-2222222222', 'residence', 'lived together',
                    ['p-aaaaaaaaaa', 'p-bbbbbbbbbb'])
        self.conn.execute(
            "INSERT INTO relationships(person_id, rel, other_id, claim_id) "
            "VALUES ('p-aaaaaaaaaa','spouse','p-bbbbbbbbbb','c-1111111111')"
        )
        # Alice/Carol share a source with no edge — should show as co-occurring.
        _add_claim(self.conn, 'c-3333333333', 's-1111111111', 'residence', 'lived together',
                    ['p-aaaaaaaaaa', 'p-cccccccccc'])
        self.conn.commit()

        rc, out = _run(find.run_related, 'p-aaaaaaaaaa', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertNotIn('Bob', out.split('co-occurring persons')[1])
        self.assertIn('Carol [p-cccccccccc] — 1 shared source(s)', out)

    def test_places_ranked_by_frequency(self) -> None:
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'residence', 'lived in Topeka',
                    ['p-aaaaaaaaaa'], place_text='Topeka, Kansas')
        _add_claim(self.conn, 'c-2222222222', 's-2222222222', 'residence', 'lived in Topeka',
                    ['p-aaaaaaaaaa'], place_text='Topeka, Kansas')
        self.conn.commit()

        rc, out = _run(find.run_related, 'p-aaaaaaaaaa', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('Topeka, Kansas — 2 claim(s)', out)

    def test_shared_affiliation_hub(self) -> None:
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'occupation',
                    'bookkeeper, Plains Junction Railroad', ['p-aaaaaaaaaa'])
        _add_claim(self.conn, 'c-2222222222', 's-2222222222', 'occupation',
                    'conductor, Plains Junction Railroad', ['p-bbbbbbbbbb'])
        self.conn.commit()

        rc, out = _run(find.run_related, 'p-aaaaaaaaaa', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('Plains Junction Railroad [occupation] — also: Bob [p-bbbbbbbbbb]', out)

    def test_date_filter_narrows_relationships_and_places(self) -> None:
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'relationship',
                    'Alice child of Bob', ['p-aaaaaaaaaa', 'p-bbbbbbbbbb'],
                    subtype='child-of', date_edtf='1900', date_min='1900-01-01', date_max='1900-12-31')
        # date_start/date_end mirror what _derive_relationships actually
        # writes for a child-of edge: the originating claim's own bounds.
        self.conn.execute(
            "INSERT INTO relationships(person_id, rel, other_id, claim_id, date_start, date_end) "
            "VALUES ('p-aaaaaaaaaa','parent','p-bbbbbbbbbb','c-1111111111','1900-01-01','1900-12-31')"
        )
        self.conn.commit()

        rc, out = _run(find.run_related, 'p-aaaaaaaaaa', '1850', self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('relationships: none', out)

        rc, out = _run(find.run_related, 'p-aaaaaaaaaa', '1900', self.archive_root, {})
        self.assertIn('parent: Bob [p-bbbbbbbbbb] — 1 source(s)', out)

    def test_date_filter_uses_relationship_validity_not_claim_bounds(self) -> None:
        # Married in 1850 (the marriage claim's own bounds are just that
        # year), still married in 1865 — date_end stays NULL (open-ended)
        # because no divorce/death claim ended it. A --date query for 1865
        # must still find the spouse edge even though it falls outside the
        # marriage claim's own narrow date_min/date_max.
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'marriage',
                    'Alice married Bob', ['p-aaaaaaaaaa', 'p-bbbbbbbbbb'],
                    date_edtf='1850', date_min='1850-01-01', date_max='1850-12-31')
        self.conn.execute(
            "INSERT INTO relationships(person_id, rel, other_id, claim_id, date_start, date_end) "
            "VALUES ('p-aaaaaaaaaa','spouse','p-bbbbbbbbbb','c-1111111111','1850-01-01',NULL)"
        )
        self.conn.commit()

        rc, out = _run(find.run_related, 'p-aaaaaaaaaa', '1865', self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('spouse: Bob [p-bbbbbbbbbb] — 1 source(s)', out)

        rc, out = _run(find.run_related, 'p-aaaaaaaaaa', '1840', self.archive_root, {})
        self.assertIn('relationships: none', out)

    def test_unknown_person_returns_warning(self) -> None:
        rc, out = _run(find.run_related, 'p-zzzzzzzzzz', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_WARNINGS)
        self.assertIn('not found in index', out)


class RelatedPlaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self._tmp.name)
        self.conn = _make_index(self.archive_root)
        _add_person(self.conn, 'p-aaaaaaaaaa', 'Alice')
        _add_source(self.conn, 's-1111111111', 'Census')
        self.conn.execute(
            "INSERT INTO places(id, name, lat, lon) VALUES ('l-1111111111', 'Fairview', 39.0, -95.0)"
        )
        self.conn.execute(
            "INSERT INTO places(id, name, within) VALUES ('l-2222222222', 'Fairview Cemetery', 'l-1111111111')"
        )

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_claims_people_and_micro_places(self) -> None:
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'residence', 'lived in Fairview',
                    ['p-aaaaaaaaaa'], place_id='l-1111111111')
        self.conn.commit()

        rc, out = _run(find.run_related, 'l-1111111111', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('c-1111111111', out)
        self.assertIn('Alice [p-aaaaaaaaaa] — 1 claim(s)', out)
        self.assertIn('Fairview Cemetery [l-2222222222]', out)

    def test_unknown_place_returns_warning(self) -> None:
        rc, out = _run(find.run_related, 'l-9999999999', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_WARNINGS)


class RelatedSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self._tmp.name)
        self.conn = _make_index(self.archive_root)
        _add_person(self.conn, 'p-aaaaaaaaaa', 'Alice')
        _add_source(self.conn, 's-1111111111', 'Census', repository='County Archive')
        _add_source(self.conn, 's-2222222222', 'Obituary', repository='County Archive')

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_claims_persons_and_sibling_by_repository(self) -> None:
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'residence', 'lived there',
                    ['p-aaaaaaaaaa'])
        self.conn.commit()

        rc, out = _run(find.run_related, 's-1111111111', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('Alice [p-aaaaaaaaaa]', out)
        self.assertIn('s-2222222222', out)

    def test_corroborating_source_via_claim_links(self) -> None:
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'birth', 'born 1900',
                    ['p-aaaaaaaaaa'])
        _add_claim(self.conn, 'c-2222222222', 's-2222222222', 'birth', 'born 1900',
                    ['p-aaaaaaaaaa'])
        self.conn.execute(
            "INSERT INTO claim_links(claim_id, rel, target_id) VALUES "
            "('c-1111111111','corroborates','c-2222222222')"
        )
        self.conn.commit()

        rc, out = _run(find.run_related, 's-1111111111', None, self.archive_root, {})
        self.assertIn('corroborates: s-2222222222', out)

        rc, out = _run(find.run_related, 's-2222222222', None, self.archive_root, {})
        self.assertIn('corroborated-by: s-1111111111', out)


class RelatedClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self._tmp.name)
        self.conn = _make_index(self.archive_root)
        _add_person(self.conn, 'p-aaaaaaaaaa', 'Alice')
        _add_source(self.conn, 's-1111111111', 'Census')
        _add_source(self.conn, 's-2222222222', 'Obituary')

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_sibling_claims_same_person_and_type(self) -> None:
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'residence', 'lived in Topeka',
                    ['p-aaaaaaaaaa'])
        _add_claim(self.conn, 'c-2222222222', 's-2222222222', 'residence', 'lived in Wichita',
                    ['p-aaaaaaaaaa'])
        self.conn.commit()

        rc, out = _run(find.run_related, 'c-1111111111', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('sibling claims (same person + type) (1):', out)
        self.assertIn('c-2222222222', out)

    def test_unknown_claim_returns_warning(self) -> None:
        rc, out = _run(find.run_related, 'c-9999999999', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_WARNINGS)


class RelatedHypothesisTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self._tmp.name)
        self.conn = _make_index(self.archive_root)
        _add_person(self.conn, 'p-aaaaaaaaaa', 'Alice')
        _add_source(self.conn, 's-1111111111', 'Census')

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_no_table_row_derives_from_referencing_claims(self) -> None:
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'note', 'maybe related',
                    ['p-aaaaaaaaaa'], status='suggested', hypothesis='h-1111111111')
        self.conn.commit()

        rc, out = _run(find.run_related, 'h-1111111111', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('hypothesis indexing is deferred', out)
        self.assertIn('Alice [p-aaaaaaaaaa]', out)
        self.assertIn('c-1111111111', out)

    def test_no_row_and_no_claims_returns_warning(self) -> None:
        rc, out = _run(find.run_related, 'h-9999999999', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_WARNINGS)


class RelatedDateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self._tmp.name)
        self.conn = _make_index(self.archive_root)
        _add_person(self.conn, 'p-aaaaaaaaaa', 'Alice')
        _add_person(self.conn, 'p-bbbbbbbbbb', 'Bob')
        _add_source(self.conn, 's-1111111111', 'Census')

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_standalone_time_slice(self) -> None:
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'residence', 'lived there',
                    ['p-aaaaaaaaaa', 'p-bbbbbbbbbb'], place_text='Topeka',
                    date_edtf='1880', date_min='1880-01-01', date_max='1880-12-31')
        self.conn.commit()

        rc, out = _run(find.run_related, None, '1880', self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('Active in 1880: 1 claims, 2 people, 1 sources', out)
        self.assertIn('Alice [p-aaaaaaaaaa]', out)
        self.assertIn('Topeka', out)

    def test_undated_claim_counts_as_unbounded_not_excluded(self) -> None:
        # index.py stores an undated claim's date_min/date_max as '' (see
        # _overlap_clause's docstring in find.py) rather than NULL or the
        # unbounded edtf_bounds() sentinel. A naive `date_max >= ?` filter
        # would treat '' as the smallest possible value and wrongly drop
        # every undated claim from every --date query.
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'residence', 'lived there',
                    ['p-aaaaaaaaaa'], date_min='', date_max='')
        self.conn.commit()

        rc, out = _run(find.run_related, None, '1900', self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('Active in 1900: 1 claims, 1 people, 1 sources', out)

    def test_no_claims_in_range(self) -> None:
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'residence', 'lived there',
                    ['p-aaaaaaaaaa'], date_edtf='1880', date_min='1880-01-01', date_max='1880-12-31')
        self.conn.commit()

        rc, out = _run(find.run_related, None, '1950', self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('Active in 1950: 0 claims, 0 people, 0 sources', out)


class RelatedValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self._tmp.name)
        self.conn = _make_index(self.archive_root)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_invalid_id_rejected(self) -> None:
        rc, out = _run(find.run_related, 'X-bad', None, self.archive_root, {})
        self.assertEqual(rc, EXIT_FAILURE)

    def test_invalid_edtf_rejected(self) -> None:
        rc, out = _run(find.run_related, None, 'not-a-date', self.archive_root, {})
        self.assertEqual(rc, EXIT_FAILURE)

    def test_neither_id_nor_date_rejected(self) -> None:
        rc, out = _run(find.run_related, None, None, self.archive_root, {})
        self.assertEqual(rc, EXIT_FAILURE)

    def test_absent_index_returns_failure(self) -> None:
        empty_root = Path(tempfile.mkdtemp())
        try:
            rc, out = _run(find.run_related, 'p-aaaaaaaaaa', None, empty_root, {})
            self.assertEqual(rc, EXIT_FAILURE)
        finally:
            import shutil
            shutil.rmtree(empty_root, ignore_errors=True)


class RunFindDispatchTests(unittest.TestCase):
    """CLI-layer dispatch: --related with/without an ID, --date alone, and the
    flag-not-given sentinel that keeps bare lookups and --text working."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self._tmp.name)
        self.conn = _make_index(self.archive_root)
        _add_person(self.conn, 'p-aaaaaaaaaa', 'Alice')
        _add_source(self.conn, 's-1111111111', 'Census')
        _add_claim(self.conn, 'c-1111111111', 's-1111111111', 'residence', 'lived there',
                    ['p-aaaaaaaaaa'])
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_related_with_id_routes_to_run_related(self) -> None:
        rc, out = _run(
            find.run_find, None, self.archive_root, {},
            related_id='p-aaaaaaaaaa', related_requested=True,
        )
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn("p-aaaaaaaaaa's world", out)

    def test_related_requested_with_no_id_is_standalone_date(self) -> None:
        rc, out = _run(
            find.run_find, None, self.archive_root, {},
            related_id=None, related_requested=True, date_filter='1850/2100',
        )
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('Active in 1850/2100', out)

    def test_bare_id_lookup_unaffected(self) -> None:
        rc, out = _run(find.run_find, 'p-aaaaaaaaaa', self.archive_root, {})
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn('p-aaaaaaaaaa  [Alice]', out)

    def _parse(self, argv: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers()
        find.register(subs)
        args = parser.parse_args(['find', *argv])
        args.root = str(self.archive_root)
        return args

    def test_date_without_related_is_rejected_not_silently_dropped(self) -> None:
        # `fha find P-id --date EDTF` (no --related) has no defined meaning —
        # it must not silently discard the ID and run the standalone
        # --related --date time-slice instead.
        args = self._parse(['p-aaaaaaaaaa', '--date', '1900'])
        rc, out = _run(find._run_find, args)
        self.assertEqual(rc, EXIT_FAILURE)

    def test_related_with_date_still_works_via_cli(self) -> None:
        args = self._parse(['--related', 'p-aaaaaaaaaa', '--date', '1900'])
        rc, out = _run(find._run_find, args)
        self.assertEqual(rc, EXIT_CLEAN)
        self.assertIn("p-aaaaaaaaaa's world", out)


if __name__ == '__main__':
    unittest.main()
