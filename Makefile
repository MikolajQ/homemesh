# make test      unit and integration tests (the latter run private sshd instances)
# make install   copies into $(DESTDIR)$(PREFIX), for packaging

PREFIX ?= /usr
DESTDIR ?=
LIBDIR = $(PREFIX)/lib/homemesh
APP_ID = io.github.MikolajQ.HomeMesh

LANGUAGES = $(basename $(notdir $(wildcard po/*.po)))
MO = $(foreach lang,$(LANGUAGES),build/locale/$(lang)/LC_MESSAGES/homemesh.mo)

.PHONY: test install pot mo

test:
	python3 -m unittest discover -s tests

# The template for translators, from the _() calls and the helper's N_() marks.
pot:
	xgettext --from-code=UTF-8 --language=Python --keyword=_ --keyword=N_ \
	    --add-comments=Translators --package-name=homemesh --msgid-bugs-address=https://github.com/MikolajQ/homemesh/issues \
	    --sort-by-file -o po/homemesh.pot homemesh/*.py helper/homemesh-helper
	sed -i 's/charset=CHARSET/charset=UTF-8/' po/homemesh.pot
	for po in po/*.po; do msgmerge --quiet --update --backup=none $$po po/homemesh.pot; done

mo: $(MO)

build/locale/%/LC_MESSAGES/homemesh.mo: po/%.po
	mkdir -p $(dir $@)
	msgfmt --check -o $@ $<

install: mo
	for lang in $(LANGUAGES); do install -Dm644 build/locale/$$lang/LC_MESSAGES/homemesh.mo \
	    $(DESTDIR)$(PREFIX)/share/locale/$$lang/LC_MESSAGES/homemesh.mo; done
	install -d $(DESTDIR)$(LIBDIR)/homemesh
	install -m644 homemesh/*.py $(DESTDIR)$(LIBDIR)/homemesh/
	install -Dm755 helper/homemesh-helper $(DESTDIR)$(LIBDIR)/homemesh-helper
	install -Dm755 helper/homemesh-route $(DESTDIR)$(LIBDIR)/homemesh-route
	install -Dm755 bin/homemesh $(DESTDIR)$(PREFIX)/bin/homemesh
	install -Dm644 data/$(APP_ID).desktop $(DESTDIR)$(PREFIX)/share/applications/$(APP_ID).desktop
	install -Dm644 data/$(APP_ID).svg $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/$(APP_ID).svg
	install -Dm644 data/$(APP_ID).policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/$(APP_ID).policy
