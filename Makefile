# ari: install from this checkout, rebuild the man page, check before a commit.

PYTHON ?= 3.14
MANDIR ?= $(HOME)/.local/share/man/man1
PANDOC = pandoc -s -f markdown-smart --columns=40 -t man

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

# Tests, then the committed man page against its source.
check:
	uv run pytest -q
	@$(PANDOC) docs/ari.1.md | cmp -s - man/ari.1 || { echo "man/ari.1 is out of date: run make man"; exit 1; }
