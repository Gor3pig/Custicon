<div align="center">

<img src="data/icons/custicon-256x256.png" alt="Custicon" width="128" height="128">

# Custicon

**Un pictogramme sur l’icône de vos dossiers, dans Nautilus.**

[![Licence : GPL v3](https://img.shields.io/badge/licence-GPL--3.0-3584e4?style=for-the-badge)](COPYING)
[![GNOME](https://img.shields.io/badge/GNOME-51-4a86cf?style=for-the-badge&logo=gnome&logoColor=white)](https://www.gnome.org)
[![Python](https://img.shields.io/badge/Python-GTK4%20%C2%B7%20libadwaita-e66100?style=for-the-badge&logo=python&logoColor=white)](https://pygobject.gnome.org)
[![Flatpak](https://img.shields.io/badge/Flatpak-paquet-9141ac?style=for-the-badge&logo=flatpak&logoColor=white)](https://flatpak.org)

[English](README.md) · **Français**

[Guide d’utilisation](help/fr/README.md) · [Fonctions](#fonctions) · [Principes](#principes) · [Installation](#installation) · [Développement](#développement)

<img src="data/screenshots/fr-main.png" alt="La fenêtre principale de Custicon" width="820">

</div>

---

## Qu’est-ce que Custicon ?

Nautilus affiche les dossiers Documents, Musique ou Images avec un petit pictogramme au centre.
**Custicon permet de faire la même chose pour n’importe quel dossier.**

Le pictogramme est posé sur la **vraie icône de dossier de votre thème d’icônes actuel**, pas sur une
imitation. Si vous changez de thème, une seule action met à jour toutes vos icônes. Vous pouvez aussi teinter le dossier lui-même.

## Fonctions

<table>
<tr>
<td width="33%" valign="top">

**Choisir**<br>Un pictogramme de votre thème ou du jeu Phosphor fourni, trouvé par son nom (en anglais ou en français), ou votre propre fichier SVG ou PNG.

</td>
<td width="33%" valign="top">

**Régler**<br>Couleur automatique ou libre, taille et position, avec un aperçu en direct.

</td>
<td width="33%" valign="top">

**Teinter**<br>Donnez au dossier lui-même la couleur de votre choix, ou la couleur d’accent de votre système.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**Retirer**<br>Le dossier retrouve exactement l’icône qu’il avait avant.

</td>
<td width="33%" valign="top">

**Régénérer**<br>Toutes les icônes d’un coup, après un changement de thème ou de couleur d’accent.

</td>
<td width="33%" valign="top">

**Respecter**<br>Le contenu de vos dossiers n’est jamais modifié.

</td>
</tr>
</table>

<p align="center"><b><a href="help/fr/README.md">Lire le guide d’utilisation →</a></b><br>Tout ce que Custicon sait faire, pas à pas.</p>

## Principes

**Aucun fichier ajouté dans vos dossiers** · **Entièrement local, sans réseau** · **Fidèle à votre thème** · **Réversible**

L’icône est enregistrée dans les métadonnées GVfs que Nautilus lit déjà, et l’image générée est rangée
dans l’espace de données de l’application. Rien n’est écrit dans le dossier lui-même.

## Installation

Custicon se distribue sous forme de paquet Flatpak.

### Depuis le dépôt Flatpak (recommandé)

Fedora, Linux Mint, Pop!_OS et beaucoup d’autres distributions fournissent déjà Flatpak. Sur Ubuntu et quelques autres,
suivez d’abord le [guide officiel de votre distribution](https://flathub.org/setup), puis redémarrez votre session.

Ouvrez [custicon.flatpakref](https://gor3pig.github.io/Custicon/custicon.flatpakref) avec l’application Logiciels, ou lancez cette commande :

```sh
flatpak install --user https://gor3pig.github.io/Custicon/custicon.flatpakref
flatpak run io.github.gor3pig.Custicon
```

Flatpak propose d’ajouter Flathub pour installer l’environnement GNOME 51 si besoin. Sans l’option `--user`, l’installation est faite pour tout le système.

Custicon se met ensuite à jour comme vos autres applications : l’application Logiciels propose chaque nouvelle version, ou lancez `flatpak update`.
Chaque mise à jour est signée.

### Depuis un fichier de release

Le fichier `custicon-X.Y.Z.flatpak` joint à chaque [release](https://github.com/Gor3pig/Custicon/releases) est une solution de repli.
Il ne se met pas à jour tout seul :

```sh
sha256sum -c custicon-X.Y.Z.flatpak.sha256
flatpak install --user custicon-X.Y.Z.flatpak
```

Si vous avez installé Custicon ainsi et voulez les mises à jour automatiques, désinstallez-le avec `flatpak uninstall io.github.gor3pig.Custicon`,
puis installez-le de nouveau depuis le fichier `.flatpakref` ci-dessus. Vos styles et vos dossiers personnalisés sont conservés, tant que vous n’ajoutez pas `--delete-data`.

### Depuis les sources

Il faut `flatpak`, `flatpak-builder` et l’environnement GNOME 51 de Flathub :

```sh
flatpak install flathub org.gnome.Platform//51 org.gnome.Sdk//51
git clone https://github.com/Gor3pig/Custicon.git
cd Custicon
flatpak-builder --user --install --disable-rofiles-fuse --force-clean build-dir \
    build-aux/io.github.gor3pig.Custicon.json
flatpak run io.github.gor3pig.Custicon
```

Le paquet est construit à partir des fichiers du dépôt git (le dernier commit de la branche `main`).

### Désinstaller

```sh
flatpak uninstall --user io.github.gor3pig.Custicon
```

Avant de désinstaller, utilisez **Retirer l’icône** pour chaque dossier personnalisé : les images
générées sont stockées dans les données de l’application et disparaissent avec elles
si vous ajoutez l’option `--delete-data`.

### Permissions demandées

Custicon fonctionne dans le bac à sable Flatpak et ne demande que ce dont il a besoin :
l’affichage, l’accès à votre dossier personnel **en lecture seule** (pour voir le vrai chemin de vos
dossiers et lire vos thèmes d’icônes) et la communication avec le service de métadonnées de GVfs, qui écrit
l’icône hors du bac à sable. Le contenu de vos dossiers ne peut donc pas être modifié par Custicon.
Il n’a pas accès au réseau. Le détail est dans [docs/architecture.md](docs/architecture.md).

Le magasin d’applications signale cette permission, car elle concerne tous vos fichiers en lecture. Elle est
nécessaire : sans le vrai chemin du dossier, GVfs enregistre l’icône dans un arbre que Nautilus ne lit pas.
Si l’accès manque, Custicon l’indique et donne la commande
`flatpak override --user --filesystem=home:ro io.github.gor3pig.Custicon`.
Si le message revient, une surcharge de permissions retire peut-être l’accès : voir le
[guide](help/fr/README.md#questions-et-réponses).

## Utilisation

Le [guide d’utilisation](help/fr/README.md) décrit chaque étape. En résumé :

1. **Ajouter un dossier** (Ctrl+O) et choisir le dossier à personnaliser.
2. Choisir un pictogramme et régler l’apparence, en regardant l’aperçu.
3. **Appliquer**, puis appuyer sur **F5** dans la fenêtre de Nautilus pour voir le changement.

On peut aussi ouvrir un dossier directement depuis Nautilus (clic droit, **Ouvrir avec Custicon**), enregistrer des styles,
teinter le dossier, choisir la couleur d’accent du système, ou poser des lettres comme pictogramme. Tout est détaillé dans le guide,
que **F1** ouvre depuis l’application.

Nautilus n’actualise pas ses icônes tout seul : F5 est nécessaire après chaque application, retrait ou régénération.

## Développement

Custicon est écrit en Python avec GTK4 et Libadwaita. [docs/architecture.md](docs/architecture.md)
présente l’organisation du code. Le script `check.sh` lance tous les contrôles (tests, validation
des fichiers de l’application, traductions) :

```sh
./check.sh
```

Il demande Python 3, PyGObject, GTK4, Libadwaita, librsvg, GVfs, `gettext`, `gpg`, `desktop-file-validate`
et `appstreamcli`. Les tests de l’interface utilisent `gtk4-broadwayd`, fourni avec GTK4, et un bus de session
(`dbus-run-session` est utilisé s’il n’y en a pas).

Les messages de commit sont écrits en anglais. La publication d’une version est décrite dans [docs/publication.md](docs/publication.md).
Le projet est développé sous Fedora avec GNOME 50.

## Licences

Custicon est un logiciel libre publié sous licence [GNU GPL v3 ou ultérieure](COPYING).

Les pictogrammes Phosphor fournis dans `src/custicon/phosphor/` sont © 2023 Phosphor Icons, sous
[licence MIT](src/custicon/phosphor/LICENSE).
