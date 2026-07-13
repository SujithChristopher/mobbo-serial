import csv

from mobbo import config, storage


def test_sanitize_subject_id_strips_unsafe_chars():
    assert storage.sanitize_subject_id("john doe #1!") == "johndoe1"


def test_sanitize_subject_id_falls_back_when_empty():
    assert storage.sanitize_subject_id("###") == "subject"


def test_create_session_dir_uses_configured_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_home_dir", lambda: tmp_path)
    (tmp_path / "Documents").mkdir()

    session_dir = storage.create_session_dir("subject1")

    assert session_dir.exists()
    assert session_dir.parent == tmp_path / "Documents" / "mobbo-data"
    assert session_dir.name.startswith("subject1_")


def test_session_csv_path_matches_session_dir_name(tmp_path):
    session_dir = tmp_path / "subject1_20260713_120000"
    session_dir.mkdir()

    csv_path = storage.session_csv_path(session_dir)

    assert csv_path == session_dir / "subject1_20260713_120000.csv"


def test_open_csv_writer_writes_header(tmp_path):
    path = tmp_path / "out.csv"

    file, writer = storage.open_csv_writer(path)
    file.close()

    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header == storage.CSV_FIELDNAMES
