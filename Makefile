# make test      unit and integration tests (the latter run private sshd instances)
# make install   copies into $(DESTDIR)$(PREFIX), for packaging

PREFIX ?= /usr
DESTDIR ?=
LIBDIR = $(PREFIX)/lib/homemesh
APP_ID = io.github.MikolajQ.HomeMesh

.PHONY: test install

test:
	python3 -m unittest discover -s tests

install:
	install -d $(DESTDIR)$(LIBDIR)/homemesh
	install -m644 homemesh/*.py $(DESTDIR)$(LIBDIR)/homemesh/
	install -Dm755 helper/homemesh-helper $(DESTDIR)$(LIBDIR)/homemesh-helper
	install -Dm755 helper/homemesh-route $(DESTDIR)$(LIBDIR)/homemesh-route
	install -Dm755 bin/homemesh $(DESTDIR)$(PREFIX)/bin/homemesh
	install -Dm644 data/$(APP_ID).desktop $(DESTDIR)$(PREFIX)/share/applications/$(APP_ID).desktop
	install -Dm644 data/$(APP_ID).svg $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/$(APP_ID).svg
	install -Dm644 data/$(APP_ID).policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/$(APP_ID).policy
