# Copyright (c) 2009-2010, Cloud Matrix Pty. Ltd.
#  All rights reserved; available under the terms of the BSD License.

from __future__ import with_statement

import os
import re
import sys
import tempfile
import urlparse
import urllib2
import subprocess
import shutil

from myppy.util import md5file, do, bt, cd, relpath, tempdir

from myppy.recipes import base


class Recipe(base.Recipe):

    ISYSROOT = "/Developer/SDKs/MacOSX10.4u.sdk"

    @property
    def TARGET_ARCHS(self):
       return self.target.TARGET_ARCHS

    @property
    def CC(self):
        return "/usr/bin/gcc-4.0"

    @property
    def CXX(self):
        return "/usr/bin/g++-4.0"

    @property
    def LOCAL_ARCH(self):
        return self.target.bt("/usr/bin/arch").strip()

    @property
    def LDFLAGS(self):
        return "-L" + os.path.join(self.PREFIX,"lib") + " -lstdc++"

    @property
    def INCFLAGS(self):
        return "-I" + os.path.join(self.PREFIX,"include")

    @property
    def CFLAGS(self):
        archflags = " ".join("-arch "+arch for arch in self.TARGET_ARCHS)
        return "-DNDEBUG -Os %s %s -mmacosx-version-min=10.4 -isysroot %s" % (archflags,self.INCFLAGS,self.ISYSROOT,)

    @property
    def CXXFLAGS(self):
        archflags = " ".join("-arch "+arch for arch in self.TARGET_ARCHS)
        return "-DNDEBUG -Os %s %s -mmacosx-version-min=10.4 -isysroot %s" % (archflags,self.INCFLAGS,self.ISYSROOT,)

    @property
    def CONFIGURE_VARS(self):
        return ["CC="+self.CC,
                "CXX="+self.CXX,
                "LDFLAGS="+self.LDFLAGS,
                "CFLAGS="+self.CFLAGS,
                "CXXFLAGS="+self.CXXFLAGS,]

    @property
    def MAKE_VARS(self):
        return ["CFLAGS="+self.CFLAGS]

    @property
    def DYLD_FALLBACK_LIBRARY_PATH(self):
        return os.path.join(self.PREFIX,"lib")

    def _generic_configure(self,script=None,vars=None,args=None,env={}):
        if vars is None and self.CONFIGURE_VARS is None:
            env = env.copy()
            env.setdefault("CC",self.CC)
            env.setdefault("CXX",self.CXX)
            env.setdefault("LDFLAGS",self.LDFLAGS)
            env.setdefault("CFLAGS",self.CFLAGS)
            env.setdefault("CXXFLAGS",self.CXXFLAGS)
        super(Recipe,self)._generic_configure(script,vars,args,env)

    def _generic_make(self,vars=None,relpath=None,target=None,makefile=None,env={}):
        """Do a generic "make" for this recipe."""
        workdir = self._get_builddir()
        if vars is None:
            vars = self.MAKE_VARS
        if relpath is None:
            relpath = self.MAKE_RELPATH
        cmd = ["make"]
        if vars is not None:
            cmd.extend(["CC="+self.CC,"CXX="+self.CXX])
            cmd.extend(vars)
        if makefile is not None:
            cmd.extend(("-f",makefile))
        cmd.extend(("-C",os.path.join(workdir,relpath)))
        if target is not None:
            cmd.append(target)
        env = env.copy()
        env.setdefault("DYLD_FALLBACK_LIBRARY_PATH",self.DYLD_FALLBACK_LIBRARY_PATH)
        self.target.do(*cmd,env=env)

    def _get_builddir(self):
        """Get the directory in which we build the given tarball.

        This is always <PREFIX>/tmp/build/<tarballname>/<srcdirname>/
        """
        #  For n-way builds there will be more than one directory in the
        #  root build dir.  All but one will be named after an arch, so
        #  that must be the one we're looking for.
        src = self.SOURCE_URL
        workdir = os.path.join(self.target.builddir,os.path.basename(src))
        for nm in os.listdir(workdir):
            if nm in self.TARGET_ARCHS:
                continue
            if nm in ("fat",):
                continue
            return os.path.join(workdir,nm)
        raise RuntimeError("no build dir")


class NWayRecipe(Recipe):
    """Build arch-specific versions independently, then merge them together.

    This recipe can be used for libs that don't like being build with multiple
    -arch flags.  It compiles each arch independently and then merges them
    together into a single set of files.
    """

    @property
    def CC(self):
        return "/usr/bin/gcc-4.0 -Os -mmacosx-version-min=10.4 -arch %s -isysroot %s" % (self.TARGET_ARCH,self.ISYSROOT,)

    @property
    def CXX(self):
        return "/usr/bin/g++-4.0 -Os -mmacosx-version-min=10.4 -arch %s -isysroot %s" % (self.TARGET_ARCH,self.ISYSROOT,)

    @property
    def CFLAGS(self):
        return self.INCFLAGS

    @property
    def CXXFLAGS(self):
        return self.INCFLAGS

    def _configure(self):
        self._nway_configure()

    def _make(self):
        self._nway_make()
        self._nway_merge()

    def _nway_configure(self,script=None,vars=None,args=None,env={}):
        """Do a "./configure" for each architecure in a separate dir."""
        workdir = self._get_builddir()
        #  Create work dir for each additional arch.
        archdirs = []
        for arch in self.TARGET_ARCHS:
            if arch == self.LOCAL_ARCH:
                archdirs.append((arch,workdir))
            else:
                archdir = os.path.join(os.path.dirname(workdir),arch)
                if os.path.exists(archdir):
                    shutil.rmtree(archdir)
                shutil.copytree(workdir,archdir)
                archdirs.append((arch,archdir))
        #  Now run the appropriate ./configure in each arch dir.
        for (arch,archdir) in archdirs:
            if os.path.exists(os.path.join(archdir,"Makefile")):
                with cd(archdir):
                    try:
                        self.target.do("make","clean")
                    except subprocess.CalledProcessError:
                        pass
            self.TARGET_ARCH = arch
            self.CONFIGURE_DIR = archdir
            self._generic_configure(script,vars,args,env)
        self.TARGET_ARCH = None

    def _nway_make(self,vars=None,relpath=""):
        """Do a generic "make" separate for each architecture."""
        workdir = self._get_builddir()
        for arch in self.TARGET_ARCHS:
            if arch == self.LOCAL_ARCH:
                continue
            archdir = os.path.join(os.path.dirname(workdir),arch)
            if not os.path.exists(archdir):
                shutil.copytree(workdir,archdir)
            self.TARGET_ARCH = arch
            print "BUILDING FOR ARCH", self.TARGET_ARCH
            nway_relpath = os.path.join(workdir,archdir)
            self._generic_make(vars,nway_relpath)
        self.TARGET_ARCH = self.LOCAL_ARCH
        print "BUILDING FOR ARCH", self.TARGET_ARCH
        self._generic_make(vars)

    def _nway_merge(self,relpath="."):
        """Merge separately-compiled archs into fat binaries."""
        workdir = self._get_builddir()
        #  Create the fat binaries in a separate dir
        fatdir = os.path.join(os.path.dirname(workdir),"fat")
        for (dirnm,_,filenms) in os.walk(os.path.join(workdir,relpath)):
            for nm in filenms:
                filepath = os.path.join(dirnm,nm)
                ext = nm.rsplit(".",1)[-1]
                if ext not in ("dylib","so","o","a"):
                    if "Mach-O" not in self.target.bt("file",filepath):
                        continue
                relfilepath = filepath[len(workdir):]
                if not os.path.isdir(os.path.dirname(fatdir+relfilepath)):
                    os.makedirs(os.path.dirname(fatdir+relfilepath))
                cmd = ["lipo","-create"]
                for arch in self.TARGET_ARCHS:
                    if arch == self.LOCAL_ARCH:
                        archdir = workdir
                    else:
                        archdir = os.path.join(os.path.dirname(workdir),arch)
                    cmd.append("-arch")
                    cmd.append(arch)
                    cmd.append(archdir+relfilepath)
                cmd.append("-output")
                cmd.append(fatdir+relfilepath)
                print "NWAY MERGE", relfilepath
                self.target.do(*cmd)
                shutil.copystat(workdir+relfilepath,fatdir+relfilepath)
        # Now copy them back into the main build dir
        for (dirnm,_,filenms) in os.walk(fatdir):
            for nm in filenms:
                filepath = os.path.join(dirnm,nm)
                relfilepath = filepath[len(fatdir):]
                shutil.copy2(filepath,workdir+relfilepath)


class CMakeRecipe(base.CMakeRecipe,Recipe):
    def _generic_cmake(self,relpath=".",args=[],env={}):
        """Do a generic "cmake" on the given source tarball."""
        archflags = " ".join("-arch "+arch for arch in self.TARGET_ARCHS)
        workdir = self._get_builddir()
        cmd = ["cmake"]
        cmd.append("-DCMAKE_INSTALL_PREFIX=%s" % (self.PREFIX,))
        cmd.append("-DCMAKE_VERBOSE_MAKEFILE=ON")
        cmd.append("-DCMAKE_OSX_SYSROOT="+self.ISYSROOT)
        cmd.append("-DCMAKE_OSX_ARCHITECTURES="+";".join(self.TARGET_ARCHS))
        cmd.append("-DBUILD_TESTS=False")
        cmd.append("-DCMAKE_BUILD_TYPE=MinSizeRel")
        for arg in args:
            cmd.append(arg)
        libdir = os.path.join(self.PREFIX,"lib")
        incdir = os.path.join(self.PREFIX,"include")
        env = env.copy()
        env.setdefault("LDFLAGS",self.LDFLAGS)
        env.setdefault("CFLAGS","-Os %s %s -mmacosx-version-min=10.4 -isysroot %s" % (archflags,self.INCFLAGS,self.ISYSROOT,))
        env.setdefault("CXXFLAGS","-Os %s %s -mmacosx-version-min=10.4 -isysroot %s" % (archflags,self.INCFLAGS,self.ISYSROOT,))
        with cd(workdir):
            self.target.do(*cmd,env=env)



class PyCMakeRecipe(base.PyCMakeRecipe,CMakeRecipe):
    pass


class PyRecipe(base.PyRecipe,Recipe):
    pass


class python26(base.python26,Recipe):
    """Install the basic Python interpreter, with myppy support."""

    @property
    def CC(self):
        return "/usr/bin/gcc-4.0 -Os -L%s -lz -mmacosx-version-min=10.4 -isysroot %s" % (os.path.join(self.PREFIX,"lib"),self.ISYSROOT,)

    @property
    def CONFIGURE_ARGS(self):
        #  We install *everything* under the Python.framework directory, which
        #  makes python install symlinks that point to themselves.  Use a fake
        #  prefix to avoid this, then just delete it later.
        fwdir = os.path.join(self.target.rootdir,"Contents","Frameworks")
        return ["--enable-universalsdk",
                "--enable-framework="+fwdir,
                "--prefix="+os.path.join(self.target.rootdir,"fake-prefix")]

    def _patch(self):
        super(python26,self)._patch()
        #  The standard config scripts can't handle repeated -arch flags in
        #  CFLAGS.  Patch them to ignore the duplicates.
        def handle_duplicate_arch_names(lines):
            for ln in lines:
                if ln.strip() == "archs.sort()":
                    yield " "*ln.index("archs")
                    yield "archs = list(set(archs))\n"
                    yield ln
                else:
                    yield ln
        self._patch_build_file("Lib/distutils/sysconfig.py",handle_duplicate_arch_names)
        self._patch_build_file("Lib/distutils/util.py",handle_duplicate_arch_names)
        def set_python_apps_dir(lines):
            for ln in lines:
                if ln.startswith("PYTHONAPPSDIR="):
                    yield "PYTHONAPPSDIR=" + self.PREFIX + "\n"
                else:
                    yield ln
        self._patch_build_file("Mac/Makefile.in",set_python_apps_dir)
        self._patch_build_file("Mac/IDLE/Makefile.in",set_python_apps_dir)
        self._patch_build_file("Mac/PythonLauncher/Makefile.in",set_python_apps_dir)

    def install(self):
        super(python26,self).install()
        os.symlink("../Python",os.path.join(self.PREFIX,"lib","libpython2.6.dylib"))
        shutil.rmtree(os.path.join(self.target.rootdir,"fake-prefix"))


class lib_sqlite3(base.lib_sqlite3,NWayRecipe):
    @property
    def MAKE_VARS(self):
        return ["CFLAGS=-DSQLITE_ENABLE_LOCKING_STYLE=0 "+self.CFLAGS]


class lib_wxwidgets_base(base.lib_wxwidgets_base,NWayRecipe):
    def _patch(self):
        #  Some typecasts don't seem to work quite right with the 10.4u SDK.
        #  Specifically, selecting between int and size_t.
        #  Add some explicit casts that should make it work OK.
        def add_explicit_casts(lines):
            for ln in lines:
                ln = re.sub(r"\[(\d+)u\]",r"[(size_t)\1u]",ln)
                ln = re.sub(r"\[(\d+)U\]",r"[(size_t)\1U]",ln)
                ln = re.sub(r"\[\(unsigned int\)([^\]]+)\]",r"[(int)\1]",ln)
                ln = re.sub(r"\[i]",r"[(size_t)i]",ln)
                yield ln
        workdir = self._get_builddir()
        for (dirnm,_,filenms) in os.walk(workdir):
            for nm in filenms:
                if nm.rsplit(".",1)[-1] not in ("h","cpp"):
                    continue
                filepath = os.path.join(dirnm,nm)[len(workdir)+1:]
                self._patch_build_file(filepath,add_explicit_casts)


class lib_wxwidgets_gizmos(base.lib_wxwidgets_gizmos,lib_wxwidgets_base):
    pass


class lib_wxwidgets_stc(base.lib_wxwidgets_stc,lib_wxwidgets_base):
    pass

class lib_wxwidgets(base.lib_wxwidgets_stc,NWayRecipe):
    pass


class py_wxpython(base.py_wxpython,Recipe):
    def install(self):
        wxconfig = os.path.join(self.PREFIX,"bin","wx-config")
        self._generic_pyinstall(relpath="wxPython",args=["WX_CONFIG="+wxconfig])


class lib_jpeg(base.lib_jpeg,NWayRecipe):
    pass


class lib_png(base.lib_png,NWayRecipe):
    pass


class lib_tiff(base.lib_tiff,NWayRecipe):
    pass


class lib_zlib(base.lib_zlib,NWayRecipe):
    pass

class lib_bz2(base.lib_bz2,NWayRecipe):
    pass


class _lib_qt4_base(base._lib_qt4_base,Recipe):
    DEPENDENCIES = ["lib_icu"]
    @property
    def CONFIGURE_ARGS(self):
        args = list(super(_lib_qt4_base,self).CONFIGURE_ARGS)
        #  Must build carbon when targeting 10.4
        args.extend(["-no-framework","-universal","-sdk",self.ISYSROOT,"-v",
                     "-platform","macx-g++40","-carbon"])
        return args


class lib_qt4(base.lib_qt4,_lib_qt4_base):
    def install(self):
        super(lib_qt4,self).install()
        #  Copy the menu.nib bundle into the app resource directory.
        #  Otherwise Qt can't find it and complains.
        workdir = self._get_builddir()
        menunib_in = os.path.join(workdir,"src/gui/mac/qt_menu.nib")
        menunib_out = os.path.join(self.target.rootdir,"Contents","Resources","qt_menu.nib")
        shutil.copytree(menunib_in,menunib_out)


class lib_qt4_full(base.lib_qt4_full,_lib_qt4_base):
    pass


class lib_icu(Recipe):
    # TODO: hardcode charset to utf8 for extra performance
    SOURCE_URL = "http://download.icu-project.org/files/icu4c/4.6/icu4c-4_6-src.tgz"
    CONFIGURE_SCRIPT = "./source/configure"
    @property
    def LDFLAGS(self):
        flags = super(lib_icu,self).LDFLAGS
        flags += " -headerpad_max_install_names"
        return flags
    


class lib_xml2(base.lib_xml2,NWayRecipe):
    pass

class lib_xslt(base.lib_xslt,NWayRecipe):
    pass


class py_modulegraph(PyRecipe):
    SOURCE_URL = "http://pypi.python.org/packages/source/m/modulegraph/modulegraph-0.8.tar.gz"

class py_altgraph(PyRecipe):
    SOURCE_URL = "http://pypi.python.org/packages/source/a/altgraph/altgraph-0.8.tar.gz"

class py_macholib(PyRecipe):
    SOURCE_URL = "http://pypi.python.org/packages/source/m/macholib/macholib-1.3.tar.gz"
    def _patch(self):
        workdir = self._get_builddir()
        patchfile = os.path.join(os.path.dirname(__file__),"_macholib.patch")
        with open(patchfile,"rb") as fin:
            with cd(os.path.join(workdir,"macholib")):
                self.target.do("patch",stdin=fin)

class py_py2app(PyRecipe):
    DEPENDENCIES = ["py_altgraph","py_modulegraph","py_macholib"]
    SOURCE_URL = "http://pypi.python.org/packages/source/p/py2app/py2app-0.5.2.tar.gz"
    def _patch(self):
        workdir = self._get_builddir()
        patchfile = os.path.join(os.path.dirname(__file__),"_py2app.patch")
        with open(patchfile,"rb") as fin:
            with cd(os.path.join(workdir,"py2app")):
                self.target.do("patch",stdin=fin)


class py_PIL(base.py_PIL,PyRecipe):
    SOURCE_URL = "http://effbot.org/media/downloads/PIL-1.1.7.tar.gz"
    def _patch(self):
        super(py_PIL,self)._patch()
        def dont_use_system_frameworks(lines):
            for ln in lines:
                if ln.strip().startswith("add_directory("):
                    if "/include" in ln or "/lib" in ln:
                        yield " "*(ln.index("a")) + "pass\n"
                    else:
                        yield ln
                else:
                    yield ln
                    if ln.strip() == "for root in framework_roots:":
                        yield " "*(ln.index("f")+4) + "break\n"
        self._patch_build_file("setup.py",dont_use_system_frameworks)


class py_pypy(base.py_pypy,Recipe):
    def _patch(self):
        super(py_pypy,self)._patch()
        def dont_use_WCOREDUMP(lines):
            for ln in lines:
                if ln.strip() == "if hasattr(os, 'WCOREDUMP'):":
                    yield ln.replace("if ","if False and ")
                else:
                    yield ln
        self._patch_build_file("pypy/rpython/module/ll_os.py",dont_use_WCOREDUMP)
        def dont_duplicate_defns(lines):
            yield "#define PYPY_NOT_MAIN_FILE"
            for ln in lines:
                  yield ln
        self._patch_build_file("pypy/translator/c/src/debug_print.c",dont_duplicate_defns)



class lib_apiextractor(base.lib_apiextractor,CMakeRecipe):
    @property
    def LDFLAGS(self):
        libdir = os.path.join(lib_qt4_full(self.target).INSTALL_PREFIX,"lib")
        flags = super(lib_apiextractor,self).LDFLAGS
        flags = ("-L%s -lQtNetwork -lQtCore -lz -framework CoreFoundation -framework Carbon -framework SystemConfiguration " % (libdir,)) + flags
        return flags

class lib_generatorrunner(base.lib_generatorrunner,CMakeRecipe):
    @property
    def LDFLAGS(self):
        libdir = os.path.join(lib_qt4_full(self.target).INSTALL_PREFIX,"lib")
        flags = super(lib_generatorrunner,self).LDFLAGS
        flags = ("-L%s -lQtNetwork -lQtCore -lz -framework CoreFoundation -framework Carbon -framework SystemConfiguration " % (libdir,)) + flags
        return flags

