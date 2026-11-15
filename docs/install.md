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

Custicon runs in the Flatpak sandbox and asks for exactly this, no more (`check.sh` fails if the list changes):

| Permission | Why |
|---|---|
| `--socket=wayland`, `--socket=fallback-x11`, `--share=ipc` | Show the window (X11 only when Wayland is not available). |
| `--nosocket=x11` | Keeps the permanent X11 socket out: some versions of Flatpak add it by themselves when a package asks for `fallback-x11`. |
| `--device=dri` | Draw the interface with the graphics card. Without it GTK falls back to software drawing, which is slow. |
| `--filesystem=home` | Access to the home folder, to know the real path of the folders and to read your icon themes. It is read and write because the read-only form (`home:ro`) did not work on every system (it failed on openSUSE Tumbleweed). |
| `--talk-name=org.gtk.vfs.Metadata`, `org.gtk.vfs.Daemon` | Let the GVfs service of your session write the icon outside the sandbox. |

Custicon never changes what is inside your folders. With GNOME, Cinnamon and MATE the icon is saved by the GVfs service of your session; with KDE Plasma and LXQt it is saved in a small hidden `.directory` file inside the folder, which is removed when the icon is removed. Tests check that the rest of a folder is unchanged. A Flatpak Dolphin may not see icons stored under `~/.var/app/`: not verified. It has no network access.

The app store flags the home folder permission because it covers all your files. It is required: without the real
path of the folder, GVfs saves the icon in a tree that Nautilus does not read.

A permission can always be removed by the user or the administrator (Flatseal, `flatpak override`, per user, global or system-wide).
No setting of the package can prevent it. If access to the home folder is missing, Custicon says so and gives the command
`flatpak override --user --filesystem=home io.github.gor3pig.Custicon`, which also works for an installation for all users.
If the message comes back, an override may be removing the access: see the [guide](../help/en/README.md#questions-and-answers).
