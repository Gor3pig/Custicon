# Architecture de Custicon

Ce document décrit comment Custicon fonctionne et pourquoi il est organisé ainsi. Il s’adresse à qui
veut comprendre, maintenir ou empaqueter le projet.

## Principe

Pour personnaliser l’icône d’un dossier, Custicon :

1. retrouve le **fichier réel** de l’icône `folder` du thème d’icônes actif, héritage entre thèmes compris ;
2. compose un **SVG autonome** qui contient ce dossier et, par-dessus, un pictogramme centré sur sa face ;
3. range ce SVG dans l’espace de données de l’application, sous un nom tiré de son contenu ;
4. écrit son adresse dans l’attribut `metadata::custom-icon` du dossier, un mécanisme de GVfs que
   Nautilus lit déjà.

Aucun fichier n’est ajouté ni modifié dans le dossier lui-même.

## Organisation du code

Le code est dans `src/custicon/`. La logique ne dépend de GTK que pour la résolution des thèmes et l’interface.

| Module | Rôle |
|---|---|
| `themes.py` | Résolution des icônes avec `Gtk.IconTheme`, lecture du thème actif par le portail de réglages, recherche de pictogrammes. |
| `render.py` | Rendu des SVG et des PNG avec librsvg et cairo. |
| `color.py` | Couleur de la face du dossier et teinte automatique plus foncée. |
| `compose.py` | Assemblage du SVG final, recoloration du pictogramme, nettoyage des fichiers fournis. |
| `metadata.py` | Lecture, écriture et suppression de `metadata::custom-icon`, avec relecture. |
| `store.py` | Registre JSON et images générées, rangées par empreinte du contenu. |
| `service.py` | Appliquer, réinitialiser, régénérer, lister. Seul point d’entrée de l’interface. |
| `environment.py` | Adaptation à l’exécution dans un bac à sable Flatpak. |
| `ui/` | Fenêtre, éditeur, sélecteur de pictogramme et logique de présentation. |
| `app.py`, `capture.py` | Application, et mode capture d’écran sans affichage. |

## Fidélité au thème

L’icône de base doit être celle du thème actif, quel qu’il soit. Custicon demande à `Gtk.IconTheme`
le fichier de l’icône `folder`, avec la même logique que Nautilus. Dans un bac à sable Flatpak, GTK
choisirait par défaut les icônes du **runtime** (par exemple une copie d’Adwaita parfois différente de
celle du système). Custicon restreint donc le chemin de recherche aux thèmes de l’hôte
(`/run/host/share/icons` et `/run/host/user-share/icons`).

Le nom du thème actif est lu par le portail `org.freedesktop.portal.Settings` : dans un bac à sable,
`Gio.Settings` peut renvoyer une valeur périmée.

## Composition

Le SVG produit incorpore le dossier du thème (identifiants préfixés pour éviter les collisions), plus un groupe
transformé qui place le pictogramme sur la face du dossier. Le résultat reste vectoriel. Si le thème ne fournit
qu’un PNG, celui-ci est incorporé dans l’enveloppe SVG.

La couleur automatique est déduite d’un rendu du dossier : échantillon au centre de la face, puis luminosité
× 0,74 et saturation × 1,12 (valeurs mesurées sur le couple face et pictogramme d’Adwaita).

Les fichiers SVG fournis par l’utilisateur sont nettoyés : scripts, objets étrangers et références
externes sont retirés, et la taille est limitée à 5 Mo.

## Stockage et réversibilité

- Les images générées portent le nom de l’empreinte de leur contenu. Changer l’icône d’un dossier
  change donc l’adresse enregistrée, ce qui force Nautilus à la relire. Une image partagée par plusieurs dossiers
  n’est supprimée que lorsqu’aucun dossier ne la référence plus.
- Le registre (`registry.json`) mémorise, pour chaque dossier, ses réglages, l’empreinte courante et la
  **valeur précédente** de `metadata::custom-icon`. Il est écrit de façon atomique et doublé d’une sauvegarde.
- Retirer une icône rétablit la valeur précédente, ou supprime l’attribut s’il n’y en avait pas.
  Si l’utilisateur a changé l’icône depuis Nautilus entre-temps, elle est conservée.
- Après chaque écriture, la valeur est relue et une différence est signalée comme une erreur.

## Exécution dans Flatpak

### Permissions

| Permission | Justification |
|---|---|
| `--socket=wayland`, `--socket=fallback-x11`, `--share=ipc` | Affichage GTK. |
| `--filesystem=home` | Écrire les métadonnées sur le **vrai chemin** des dossiers. Un accès plus restreint est dangereux : le point de montage vu du bac à sable diffère alors de celui de l’hôte et GVfs écrit dans un arbre que Nautilus ne lit pas, sans erreur. Les dossiers de thèmes de l’hôte sont visibles sans cette permission. |
| `--talk-name=org.gtk.vfs.Metadata` et `org.gtk.vfs.Daemon` | Communiquer avec le démon de métadonnées de GVfs. Sans les deux, l’écriture est refusée. |

Il n’y a pas d’accès au réseau, ni à `org.freedesktop.Flatpak` (pas d’évasion du bac à sable).

### Le dossier de données de GVfs

Le client GVfs construit le chemin du fichier de métadonnées à partir de `XDG_DATA_HOME`, puis le
transmet au démon de l’hôte, qui y écrit. Dans un bac à sable, cette variable désigne le dossier de
données privé de l’application : l’écriture réussissait, mais dans un fichier que Nautilus ne lit jamais.

`environment.py` redirige donc `XDG_DATA_HOME` vers le dossier de données de l’hôte (`HOST_XDG_DATA_HOME`,
ou `~/.local/share`) **dans le programme, avant tout import de GLib**. L’option `--env` du manifeste ne
suffit pas, car Flatpak réimpose la variable. Le dossier propre de l’application (registre, images) est
conservé dans `CUSTICON_DATA_HOME`. `metadata.py` refuse d’écrire si GLib n’utilise pas le dossier de l’hôte.

### Le sélecteur de dossiers

Le sélecteur de fichiers passe par le portail. Avec l’accès au dossier personnel, il renvoie le vrai chemin.
Un chemin du portail de documents (`/run/user/<id>/doc/...`) serait inutilisable pour Nautilus :
Custicon le détecte et le refuse avec un message.

## Nautilus et le rafraîchissement

Nautilus ne surveille pas les changements d’icône personnalisée : la vue doit être rechargée (F5).
L’interface le rappelle à chaque endroit où cela compte.

## Tests et contrôles

`./check.sh` lance, dans l’ordre : absence de commentaires dans le code, cohérence des versions,
validation du fichier `.desktop`, des métadonnées AppStream et du manifeste, complétude des traductions et
typographie française, tests unitaires, puis tests de l’interface sur un affichage Broadway virtuel
(jamais sur la session graphique de l’utilisateur).

Les tests qui touchent à GVfs vérifient les métadonnées réelles et se sautent si le service est absent.

## Traductions

Les textes sont en anglais dans le code et traduits avec gettext (`po/`). La langue française est complète.

## Capture d’écran

`python3 -m custicon --capture <écran> --out <fichier.png>` produit une image de l’interface à partir de données
de démonstration, sans ouvrir de fenêtre sur le bureau lorsqu’il est lancé sous Broadway. Les captures de
cette documentation sont produites ainsi.
