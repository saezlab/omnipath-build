"""Opt-in end-to-end check against the live HTTPS artifact service."""

import argparse
from pathlib import Path
import tempfile

from omnipath_client import Client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="https://omnipath-metabo-dev.schaul.click/api")
    parser.add_argument("--release", default="latest")
    args = parser.parse_args()
    columns = [
        "relation_key",
        "subject_entity_key",
        "subject_label",
        "predicate",
        "object_label",
        "_resource_version",
    ]
    with tempfile.TemporaryDirectory(prefix="omnipath-client-smoke-") as temporary:
        with Client(args.api_url, release=args.release) as client:
            records = client.resources()
            signor = next(r for r in records if r["resource_id"] == "signor")
            result = client.relations("signor", filters={"taxon": "9606"}, columns=columns)
            expected = result.order("relation_key").limit(5).fetchall()
            assert len(expected) == 5
            print(f"Remote query: 5 rows from SIGNOR {signor['version']}", flush=True)
            seed = expected[0][1]
            matches = client.lookup(seed, resources="signor").fetchall()
            assert matches
            assert client.related(seed, resources="signor").limit(1).fetchall()
            print(f"Lookup and related: passed ({len(matches)} seed matches)", flush=True)
            joined = client.sql(
                """SELECT r.relation_key FROM relation r JOIN entity e
                ON r.subject_entity_key = e.entity_key AND r._resource = e._resource
                WHERE r.relation_key = ?""",
                resources="signor",
                parameters=[expected[0][0]],
            )
            assert joined.fetchall() == [(expected[0][0],)]
            print("Parameterized SQL join: passed", flush=True)
            snapshot = client.download(Path(temporary) / "snapshot", resources="signor")
            print("Downloaded entity/relation snapshot", flush=True)
        with Client.from_snapshot(snapshot) as offline:
            actual = (
                offline.relations("signor", filters={"taxon": "9606"}, columns=columns)
                .order("relation_key")
                .limit(5)
                .fetchall()
            )
            assert actual == expected
            print("Offline query: identical results", flush=True)


if __name__ == "__main__":
    main()
