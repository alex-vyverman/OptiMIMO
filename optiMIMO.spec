# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for OptiMIMO - MIMO Room Correction GUI.

Usage:
    pyinstaller optiMIMO.spec
"""

import os
import platform
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, collect_dynamic_libs

_pyproject = tomllib.loads(Path(SPECPATH).joinpath('pyproject.toml').read_text())
_version = _pyproject['project']['version']


def _collect_extension_modules(package_name):
    """Collect all C extension .so/.pyd files from a package with correct relative paths.
    
    PyInstaller's collect_dynamic_libs() only collects shared library dependencies
    (like libgfortran, libopenblas), NOT Python C extension modules. This function
    finds all .so/.pyd files and returns them as (src, dst) tuples where dst is the
    relative directory path within the package, so they end up in the correct location.
    """
    import importlib
    mod = importlib.import_module(package_name)
    pkg_path = os.path.dirname(mod.__file__)
    parent_path = os.path.dirname(pkg_path)
    extensions = []
    for root, dirs, files in os.walk(pkg_path):
        for f in files:
            if f.endswith('.so') or f.endswith('.pyd'):
                full_path = os.path.join(root, f)
                rel_dir = os.path.dirname(os.path.relpath(full_path, parent_path))
                extensions.append((full_path, rel_dir))
    return extensions


# Detect architecture for macOS builds
if sys.platform == 'darwin':
    _target_arch = platform.machine()  # 'arm64' or 'x86_64'
else:
    _target_arch = None

block_cipher = None

# Collect all NiceGUI data files (static assets, templates, JS, CSS)
nicegui_datas, nicegui_binaries, nicegui_hiddenimports = collect_all('nicegui')

# Collect Plotly data files (templates, package data)
plotly_datas = collect_data_files('plotly')
plotly_hiddenimports = collect_submodules('plotly')

# Collect scipy: submodules, shared libs, AND C extension modules
scipy_hiddenimports = collect_submodules('scipy')
scipy_dynlibs = collect_dynamic_libs('scipy')
scipy_extensions = _collect_extension_modules('scipy')

# Collect numpy: submodules, shared libs, AND C extension modules
numpy_hiddenimports = collect_submodules('numpy')
numpy_dynlibs = collect_dynamic_libs('numpy')
numpy_extensions = _collect_extension_modules('numpy')

a = Analysis(
    ['optiMIMO_launcher.py'],
    pathex=[],
    binaries=nicegui_binaries + scipy_dynlibs + scipy_extensions + numpy_dynlibs + numpy_extensions,
    datas=nicegui_datas + plotly_datas + [
        ('optimimo', 'optimimo'),
    ],
    hiddenimports=[
        # Python built-in modules that PyInstaller sometimes misses
        'mmap',
        'ctypes',
        'ctypes.util',
        'multiprocessing',
        'multiprocessing.pool',
        'concurrent.futures',
        'concurrent.futures.thread',
        'concurrent.futures.process',
        # Application modules
        'optimimo',
        'optimimo.cli',
        'optimimo.core',
        'optimimo.core.io',
        'optimimo.core.pipeline',
        'optimimo.core.smoothing',
        'optimimo.core.solver',
        'optimimo.core.targets',
        'optimimo.export',
        'optimimo.export.camilladsp',
        'optimimo.export.firs',
        'optimimo.gui',
        'optimimo.gui.app',
        'optimimo.gui.analysis_tab',
        'optimimo.gui.config_tab',
        'optimimo.gui.file_picker',
        'optimimo.gui.measurements_tab',
        'optimimo.gui.plots',
        'optimimo.gui.run_tab',
        'optimimo.gui.state',
        'optimimo.util',
        # NiceGUI runtime dependencies
        'engineio.async_drivers.threading',
        'multipart',
        'h11',
        'httptools',
        'websockets',
        'email_validator',
        'itsdangerous',
        'orjson',
        'ujson',
        'yaml',
        # Plotly
        'plotly.io',
        'plotly.graph_objects',
        'plotly.express',
        'plotly.subplots',
        'plotly.validators',
        'plotly.colors',
        'plotly.figure_factory',
        'plotly.io._json',
        'plotly.io._html',
        'plotly.io._renderers',
        'plotly.io._templates',
        'plotly.io._utils',
        'plotly.io._base_renderers',
        'plotly.io._orca',
        'plotly.io._kaleido',
        # scipy C extensions (fix circular imports)
        'scipy.signal',
        'scipy.signal.windows',
        'scipy.fft',
        'scipy.linalg',
        'scipy.sparse',
        'scipy.sparse.linalg',
        'scipy.interpolate',
        'scipy.optimize',
        'scipy.stats',
        'scipy.ndimage',
        'scipy.special',
        'scipy.interpolate._fitpack',
        'scipy.interpolate._bspl',
        'scipy.interpolate._interpnd',
        'scipy.interpolate._ppoly',
        'scipy.interpolate._rgi_cython',
        'scipy.signal._sigtools',
        'scipy.signal._spline',
        'scipy.signal._max_len_seq_inner',
        'scipy.signal._upfirdn_apply',
        'scipy.linalg._cythonized_array_utils',
        'scipy.linalg._solve_toeplitz',
        'scipy.linalg._decomp_lu_cython',
        'scipy.linalg._matfuncs_expm',
        'scipy.sparse._sparsetools',
        'scipy.sparse.csgraph._tools',
        'scipy.sparse.csgraph._shortest_path',
        'scipy.sparse.csgraph._traversal',
        'scipy.sparse.csgraph._min_spanning_tree',
        'scipy.sparse.csgraph._flow',
        'scipy.sparse.csgraph._matching',
        'scipy.sparse.csgraph._reordering',
        'scipy.special._ufuncs',
        'scipy.special._ufuncs_cxx',
        'scipy.special._specfun',
        'scipy.special._ellip_harm_2',
        'scipy.special._comb',
        'scipy.ndimage._nd_image',
        'scipy.ndimage._ni_label',
        'scipy.optimize._group_columns',
        'scipy.optimize._trlib._trlib',
        'scipy.optimize._lbfgsb',
        'scipy.optimize._slsqp',
        'scipy.optimize._minpack',
        'scipy.optimize._zeros',
        'scipy.optimize._cobyla',
        'scipy.stats._stats',
        'scipy.stats._statlib',
        'scipy.stats._mvn',
        'scipy.stats._boost',
        'scipy.stats._biasedurn',
        'scipy.fft._pocketfft.pypocketfft',
        # numpy C extensions
        'numpy.fft._pocketfft_umath',
        'numpy.core._multiarray_umath',
        'numpy.core._multiarray_tests',
        'numpy.linalg._umath_linalg',
        'numpy.random._common',
        'numpy.random._bounded_integers',
        'numpy.random._mt19937',
        'numpy.random._philox',
        'numpy.random._pcg64',
        'numpy.random._sfc64',
        'numpy.random._generator',
        'numpy.random.bit_generator',
        'numpy.random.mtrand',
        # scipy.io.matlab Cython extensions
        'scipy.io.matlab._mio_utils',
        'scipy.io.matlab._streams',
        'scipy.io.matlab._mio5_utils',
        'scipy.io.matlab._byteorder',
        'scipy.io._idl',
        'scipy.io._mmio',
        'scipy.io.netcdf',
    ] + nicegui_hiddenimports + plotly_hiddenimports + scipy_hiddenimports + numpy_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime_hook_stdlib.py'],
    excludes=[
        'tkinter',
        'matplotlib',
        'PIL',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
        'setuptools',
        'pip',
        'distutils',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Platform-specific icon: .icns on macOS (used by the .app BUNDLE),
# .ico on Windows (embedded into the .exe).
if sys.platform == 'darwin':
    _icon_path = os.path.join(SPECPATH, 'images', 'optimimo.icns')
elif sys.platform == 'win32':
    _icon_path = os.path.join(SPECPATH, 'images', 'optimimo.ico')
else:
    _icon_path = None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='OptiMIMO',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # No terminal window when launched from Finder on macOS; the GUI lives
    # in the browser. Keep the console on other platforms so server logs
    # appear when run from a shell.
    console=False if sys.platform == 'darwin' else True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=_target_arch,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon_path,
)

if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='OptiMIMO.app',
        icon=_icon_path,
        bundle_identifier='com.alexvyverman.optimimo',
        version=_version,
        info_plist={
            'CFBundleName': 'OptiMIMO',
            'CFBundleDisplayName': 'OptiMIMO',
            'CFBundleShortVersionString': _version,
            'CFBundleVersion': _version,
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '11.0',
            'NSPrincipalClass': 'NSApplication',
        },
    )
