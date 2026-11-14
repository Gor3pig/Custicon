# Installing Custicon

The quick way is described in the [README](../README.md). This page covers the other options.

## From a release file

The `custicon-X.Y.Z.flatpak` file attached to each [release](https://github.com/Gor3pig/Custicon/releases) is an alternative.
It is not updated by itself:

```sh
sha256sum -c custicon-X.Y.Z.flatpak.sha256
flatpak install --user custicon-X.Y.Z.flatpak
```

To get automatic updates after such an install, uninstall it with `flatpak uninstall io.github.gor3pig.Custicon`
and install it again from the `.flatpakref` file. Your styles and customized folders are kept, as long as you do not add `--delete-data`.

Without the `--user` option, an installation is system-wide.

## Uninstall

```sh
flatpak uninstall --user io.github.gor3pig.Custicon
```

Before uninstalling, use **Remove Icon** on each customized folder: the generated images are stored in the
data of the application and disappear with it if you add the `--delete-data` option.

## Permissions

Custicon runs in the Flatpak sandbox and only asks for what it needs: the display, **read-only** access to your home
folder (to see the real path of your folders and read your icon themes) and communication with the GVfs metadata
service, which writes the icon outside the sandbox. The content of your folders therefore cannot be modified by Custicon.
It has no network access. Details are in [architecture.md](architecture.md) (in French).

The app store flags this permission because it covers all your files in read mode. It is required: without the real
path of the folder, GVfs saves the icon in a tree that Nautilus does not read. If access is missing, Custicon says so
and gives the command `flatpak override --user --filesystem=home:ro io.github.gor3pig.Custicon`.
If the message comes back, a permission override may be removing the access: see the
[guide](../help/en/README.md#questions-and-answers).
