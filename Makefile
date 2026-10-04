PREFIX ?= /usr/local
DESTDIR ?=
PYTHON ?= python3

BINDIR = $(PREFIX)/bin
MANDIR = $(PREFIX)/share/man/man1
APPDIR = $(PREFIX)/share/applications
DOCDIR = $(PREFIX)/share/doc/shtop
LICENSEDIR = $(PREFIX)/share/licenses/shtop

.PHONY: all check test lint install uninstall

all:
	@echo "shtop is a single Python script; there is nothing to build."
	@echo "Run 'make install' (PREFIX=$(PREFIX)) or 'make test'."

check: test

test:
	$(PYTHON) -m unittest discover -s tests -v

lint:
	ruff check .

install:
	install -Dm755 shtop "$(DESTDIR)$(BINDIR)/shtop"
	install -Dm644 shtop.1 "$(DESTDIR)$(MANDIR)/shtop.1"
	install -Dm644 shtop.desktop "$(DESTDIR)$(APPDIR)/shtop.desktop"
	install -Dm644 README.md "$(DESTDIR)$(DOCDIR)/README.md"
	install -Dm644 LICENSE "$(DESTDIR)$(LICENSEDIR)/LICENSE"

uninstall:
	rm -f "$(DESTDIR)$(BINDIR)/shtop" "$(DESTDIR)$(MANDIR)/shtop.1" \
	      "$(DESTDIR)$(APPDIR)/shtop.desktop" "$(DESTDIR)$(DOCDIR)/README.md" \
	      "$(DESTDIR)$(LICENSEDIR)/LICENSE"
	-rmdir "$(DESTDIR)$(DOCDIR)" "$(DESTDIR)$(LICENSEDIR)" 2>/dev/null
