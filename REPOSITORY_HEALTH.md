# Local repository health

Before development, run from the canonical STOCK-SCANNER directory:

```sh
python3 repository_health_check.py
```

The command detects macOS dataless/offloaded Git metadata before running a bounded
`git fsck --full`. Any unavailable metadata, integrity failure, or timeout returns
nonzero. Dangling-object notices alone do not fail the check. The command never
repairs or deletes Git data, changes refs, fetches, pins files, or changes OS settings.
Git reads can cause the storage provider to download existing file contents.

If it fails, stop development and preserve current files. Restore local availability
through the storage provider, then rerun the check. Do not initially delete packs,
rebuild indexes, reset, clean, or assume the repository needs to be recloned.

This is an explicit development preflight, not an installed hook or continuous
monitor. A pass establishes current integrity; files can still be offloaded later.
Keep a separately verified backup of uncommitted work and repository metadata.

If Finder identifies this folder as managed by iCloud Drive and offers **Keep
Downloaded**, apply that option narrowly to the STOCK-SCANNER folder, then rerun
the health check. Apple's [file availability instructions](https://support.apple.com/guide/mac-help/work-with-folders-and-files-in-icloud-drive-mchl1a02d711/mac)
describe that reversible option. The prior offloading service has not been
identified, so this is conditional guidance, not a claim that iCloud caused it.
No broad storage-optimization change or repository relocation is required here.

Detection tests use disposable Git repositories:

```sh
python3 -m unittest discover -s tests -p test_repository_health.py -v
```
