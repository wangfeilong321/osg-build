VERSION = 1.3.1
NAME = osg-build
NAME_VERSION = $(NAME)-$(VERSION)
PYDIR = osgbuild
SVNDATADIR = data
SVNDOCDIR = doc
MAIN_SCRIPT = $(NAME)
MAIN_SCRIPT_SYMLINK = vdt-build
EXTRA_SCRIPTS = koji-tag-checker koji-tag-diff osg-build-test osg-import-srpm osg-koji osg-promote
PYTHON_SITELIB = $(shell python -c "from distutils.sysconfig import get_python_lib; import sys; sys.stdout.write(get_python_lib())")
BINDIR = /usr/bin
DOCDIR = /usr/share/doc/$(NAME)
DATADIR = /usr/share/$(NAME)
AFS_SOFTWARE_DIR = /p/vdt/public/html/upstream/$(NAME)

_default:
	@echo "Nothing to make. Try make install"

clean:
	rm -f *.py[co] *~ $(PYDIR)/*.py[co] $(PYDIR)/*~ tags

install:
	@if [ "$(DESTDIR)" = "" ]; then                                        \
		echo " ";                                                      \
		echo "ERROR: DESTDIR is required";                             \
		exit 1;                                                        \
	fi

	mkdir -p $(DESTDIR)/$(PYTHON_SITELIB)/$(PYDIR)
	install -p -m 644 $(PYDIR)/* $(DESTDIR)/$(PYTHON_SITELIB)/$(PYDIR)

	mkdir -p $(DESTDIR)/$(DOCDIR)
	install -p -m 644 $(SVNDOCDIR)/* $(DESTDIR)/$(DOCDIR)

	mkdir -p $(DESTDIR)/$(BINDIR)
	install -p -m 755 $(MAIN_SCRIPT) $(DESTDIR)/$(BINDIR)
	install -p -m 755 $(EXTRA_SCRIPTS) $(DESTDIR)/$(BINDIR)
	ln -s $(MAIN_SCRIPT) $(DESTDIR)/$(BINDIR)/$(MAIN_SCRIPT_SYMLINK)

	mkdir -p $(DESTDIR)/$(DATADIR)
	install -p -m 644 $(SVNDATADIR)/* $(DESTDIR)/$(DATADIR)


dist:
	mkdir -p $(NAME_VERSION)
	cp -rp $(MAIN_SCRIPT) $(EXTRA_SCRIPTS) $(PYDIR) $(SVNDATADIR) $(SVNDOCDIR) Makefile pylintrc $(NAME_VERSION)/
	sed -i -e '/__version__/s/@VERSION@/$(VERSION)/' $(NAME_VERSION)/$(PYDIR)/main.py
	tar czf $(NAME_VERSION).tar.gz $(NAME_VERSION)/ --exclude='*/.svn*' --exclude='*/*.py[co]' --exclude='*/*~'

afsdist: dist
	mkdir -p $(AFS_SOFTWARE_DIR)/$(VERSION)
	mv -f $(NAME_VERSION).tar.gz $(AFS_SOFTWARE_DIR)/$(VERSION)/
	rm -rf $(NAME_VERSION)

release: dist
	@if [ "$(DESTDIR)" = "" ]; then                                        \
		echo " ";                                                      \
		echo "ERROR: DESTDIR is required";                             \
		exit 1;                                                        \
	fi
	mkdir -p $(DESTDIR)/$(NAME)/$(VERSION)
	mv -f $(NAME_VERSION).tar.gz $(DESTDIR)/$(NAME)/$(VERSION)/
	rm -rf $(NAME_VERSION)

check:
	pylint -E osg-build osg-build-test osg-promote osg-koji $(PYDIR)/*.py
test:
	pylint -E osg-build osg-build-test osg-promote osg-koji $(PYDIR)/*.py
	python osg-build-test -v TestSuiteAll

shorttest:
	pylint -E osg-build osg-build-test osg-promote osg-koji $(PYDIR)/*.py
	python osg-build-test -v TestSuiteShort

lint:
	-pylint --rcfile=pylintrc osg-build osg-build-test osg-promote osg-koji $(PYDIR)/*.py
# ignore return code in above

tags:
	-ctags -R --exclude='.backup' --exclude='.bak' --exclude='*~' --exclude='.svn' --exclude='_darcs' --exclude='.git' --exclude='CVS' --exclude='.pyc' --exclude='Attic/*' --exclude='data/*' --exclude='doc/*' .

.PHONY: _default clean install dist afsdist release check test shorttest lint tags
