import stat
import zipfile

import pytest

pytest.importorskip("PIL", reason="Run source security tests in the declared Studio environment")
from workflow_studio.contracts import StudioError
from workflow_studio.ingestion.folder import collect_path
from workflow_studio.ingestion.models import ImportLimits
from workflow_studio.service import StudioService


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "/outside.txt",
        "C:/outside.txt",
        "a\\b.txt",
        "safe/../../outside.txt",
        "CON.txt",
    ],
)
def test_zip_traversal_and_reserved_names_rejected(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(name, "must not escape")
    if "\\" in name:
        # Windows ZipInfo normalizes separators when writing; construct an incoming raw ZIP.
        archive.write_bytes(
            archive.read_bytes().replace(name.replace("\\", "/").encode(), name.encode())
        )
    with pytest.raises(StudioError):
        collect_path(archive, ImportLimits())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bad.zip"]


def test_zip_bomb_link_duplicate_and_nested_archive_rejected(tmp_path):
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("huge.txt", "0" * 100_000)
    with pytest.raises(StudioError):
        collect_path(archive, ImportLimits())
    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as z:
        info = zipfile.ZipInfo("link.txt")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "outside")
    with pytest.raises(StudioError):
        collect_path(archive, ImportLimits())
    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a.txt", "one")
        z.writestr("A.txt", "two")
    with pytest.raises(StudioError):
        collect_path(archive, ImportLimits())
    archive = tmp_path / "nested.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("inner.zip", b"not accepted")
    with StudioService.open_workspace(tmp_path / "workspace") as studio:
        p = studio.projects.create("安全")
        with pytest.raises(StudioError):
            studio.sources.import_path(p.project_id, 1, archive)
        assert studio.sources.list(p.project_id) == []


def test_safe_zip_produces_previews_without_disk_extraction(tmp_path):
    archive = tmp_path / "good.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("資料/說明.txt", "你好")
    with StudioService.open_workspace(tmp_path / "workspace") as studio:
        p = studio.projects.create("ZIP")
        source = studio.sources.import_path(p.project_id, 1, archive)
        assert source.manifest["files"][0]["preview"]["text"] == "你好"
    assert not (tmp_path / "資料").exists()


def test_folder_size_and_empty_limits(tmp_path):
    with pytest.raises(StudioError):
        collect_path(tmp_path, ImportLimits())
    (tmp_path / "one.txt").write_text("test")
    with pytest.raises(StudioError):
        collect_path(tmp_path, ImportLimits(max_file_bytes=2))
    (tmp_path / "two.txt").write_text("test")
    with pytest.raises(StudioError):
        collect_path(tmp_path, ImportLimits(max_files=1))


def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    folder = tmp_path / "folder"
    folder.mkdir()
    try:
        (folder / "escape.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Host does not permit creating symbolic links")
    with pytest.raises(StudioError):
        collect_path(folder, ImportLimits())
