project(
  'custicon',
  version: '1.8.1',
  license: 'GPL-3.0-or-later',
  meson_version: '>= 1.0.0',
)

i18n = import('i18n')
gnome = import('gnome')
python = import('python').find_installation('python3')

install_subdir(
  'src' / 'custicon',
  install_dir: python.get_install_dir(),
  exclude_directories: ['__pycache__', 'ui' / '__pycache__'],
  exclude_files: ['capture.py'],
)

configure_file(
  input: 'src' / 'custicon.in',
  output: 'custicon',
  configuration: {'PYTHON': python.full_path()},
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

subdir('data')
subdir('po')

gnome.post_install(
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
