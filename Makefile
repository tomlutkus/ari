# ari: install from this checkout, rebuild the man page, check before a commit.

PYTHON ?= 3.14
MANDIR ?= $(HOME)/.local/share/man/man1
PANDOC = pandoc -s -f markdown-smart --columns=40 -t man
VERSION = $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)

.PHONY: install uninstall man check

# Editable, so ari is whatever is checked out. --reinstall picks up entry point changes.
install:
	uv tool install --editable --python $(PYTHON) --reinstall .
	mkdir -p $(MANDIR)
	ln -sf $(CURDIR)/man/ari.1 $(MANDIR)/ari.1

uninstall:
	uv tool uninstall ari
	rm -f $(MANDIR)/ari.1

man:
	$(PANDOC) docs/ari.1.md -o man/ari.1

# Tests, the committed man page against its source, and one version everywhere.
check:
	uv run pytest -q
	@$(PANDOC) docs/ari.1.md | cmp -s - man/ari.1 || { echo "man/ari.1 is out of date: run make man"; exit 1; }
	@grep -q '^footer: ari $(VERSION)$$' docs/ari.1.md || { echo "docs/ari.1.md footer isn't ari $(VERSION)"; exit 1; }
	@grep -q '^## $(VERSION) ' CHANGELOG.md || { echo "CHANGELOG.md has no entry for $(VERSION)"; exit 1; }
