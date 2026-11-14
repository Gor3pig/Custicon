# Developing Custicon

Custicon is written in Python with GTK4 and Libadwaita. [architecture.md](architecture.md) (in French)
presents the organization of the code.

## Building from the sources

You need `flatpak`, `flatpak-builder` and the GNOME 51 platform from Flathub:

```sh
flatpak install flathub org.gnome.Platform//51 org.gnome.Sdk//51
git clone https://github.com/Gor3pig/Custicon.git
cd Custicon
flatpak-builder --user --install --disable-rofiles-fuse --force-clean build-dir \
    build-aux/io.github.gor3pig.Custicon.json
flatpak run io.github.gor3pig.Custicon
```

The package is built from the files of the git repository (the last commit of the `main` branch).

## Checks

The `check.sh` script runs every check (tests, validation of the application files, translations):

```sh
./check.sh
```

It needs Python 3, PyGObject, GTK4, Libadwaita, librsvg, GVfs, `gettext`, `gpg`, `desktop-file-validate`
and `appstreamcli`. The interface tests use `gtk4-broadwayd`, shipped with GTK4, and a session bus
(`dbus-run-session` is used when there is none).

## Conventions

- The code has no comments or docstrings, and `check.sh` enforces it.
- Commit messages are written in English and explain why the change is made.
- The interface is written in English and translated with gettext (`po/`). Every new string must be translated
  in `po/fr.po`, with French typography (non-breaking spaces before `: ? ! ;` and inside guillemets).
- Releases are described in [publication.md](publication.md).
