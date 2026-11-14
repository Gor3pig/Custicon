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
| `themes.py` | Résolution des icônes avec `Gtk.IconTheme`, lecture du thème actif et de la couleur d’accent par le portail de réglages, recherche de pictogrammes. |
| `textglyph.py` | Conversion de 1 à 3 caractères en tracé SVG (Pango et cairo), pour les pictogrammes en lettres. |
| `keywords.py`, `textutil.py` | Dictionnaires hors ligne mots français vers fragments de noms (thème et jeu Phosphor), et normalisation sans accents ni casse. |
| `iconsets.py`, `phosphor/`, `emoji/`, `brands/` | Registre des jeux de pictogrammes livrés (noms préfixés `phosphor:`, `emoji:`, `brand:`), chacun avec son index (étiquettes, catégories, noms français) et sa licence. Les données viennent de `build-aux/update-icon-sets.py`, épinglé sur un commit et des empreintes. |
| `suggestions.py` | Suggestions de pictogrammes d’après les mots du nom du dossier, réparties entre les jeux. |
| `render.py` | Rendu des SVG et des PNG avec librsvg et cairo. |
| `color.py` | Couleur de la face du dossier et teinte automatique plus foncée. |
| `compose.py` | Assemblage du SVG final, recoloration du pictogramme, teinte du dossier, nettoyage des fichiers fournis. |
| `metadata.py` | Lecture, écriture et suppression de `metadata::custom-icon`, avec relecture. |
| `store.py` | Registre JSON, images générées rangées par empreinte du contenu, copies des pictogrammes, apparence mémorisée (`defaults.json`), styles (`styles.json`) et pictogrammes récents et favoris (`picker.json`). |
| `service.py` | Appliquer (un dossier ou plusieurs avec `apply_many`, qui continue après un échec et rend un compte rendu), réinitialiser (un dossier ou tous), annuler un retrait, retrouver un dossier déplacé, régénérer, styles, nettoyage. Seul point d’entrée de l’interface. |
| `environment.py` | Adaptation à l’exécution dans un bac à sable Flatpak, détection de l’accès au dossier personnel. |
| `aboutinfo.py` | Notes de version lues dans la metainfo (français ou anglais) et adresse du guide selon la langue. |
| `ui/` | Fenêtre (liste, mode sélection, lot de dossiers), éditeur en deux colonnes (aperçu manipulable à la souris), palette de pastilles (`palette.py`), sélecteur de pictogramme (`Gtk.GridView`), boîte « À propos » et logique de présentation. |
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

Le SVG produit incorpore le dossier du thème, plus un groupe transformé qui place le pictogramme sur la face
du dossier. Le pictogramme SVG est inséré comme un document séparé (image imbriquée) : ses identifiants et ses
feuilles de style ne peuvent donc ni entrer en collision avec ceux du dossier ni les modifier. Le résultat reste
vectoriel. Si le thème ne fournit qu’un PNG, celui-ci est incorporé dans l’enveloppe SVG.

La **teinte du dossier** est un filtre SVG appliqué à un groupe qui enveloppe les éléments du dossier : une couleur
unie fusionnée en mode « couleur » (teinte et saturation de la couleur choisie, luminosité du dossier) puis découpée par la
forme du dossier. Le relief du dossier est donc conservé, quel que soit le thème, et le fichier du thème n’est jamais modifié.

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
| `--device=dri` | Accélération graphique : sans elle, GTK4 dessine en logiciel et l’interface devient lente (ajoutée en 1.4.1). |
| `--filesystem=home:ro` | Voir le **vrai chemin** des dossiers, en **lecture seule**. L’application ne peut donc pas modifier le contenu de vos dossiers : seul le service GVfs de l’hôte écrit la métadonnée, hors du bac à sable. Un accès plus restreint est dangereux : le point de montage vu du bac à sable diffère alors de celui de l’hôte et GVfs écrit dans un arbre que Nautilus ne lit pas, sans erreur. Les dossiers de thèmes de l’hôte sont visibles sans cette permission. Le dossier de données de l’application reste accessible en écriture. |
| `--talk-name=org.gtk.vfs.Metadata` et `org.gtk.vfs.Daemon` | Communiquer avec le démon de métadonnées de GVfs. Sans les deux, l’écriture est refusée. |

Il n’y a pas d’accès au réseau, ni à `org.freedesktop.Flatpak` (pas d’évasion du bac à sable). `check.sh` fige la liste des `finish-args`. Un utilisateur ou un administrateur peut toujours retirer une permission par une surcharge (Flatseal, `flatpak override`, y compris globale ou système) : aucune option du manifeste n’y change rien, l’application détecte donc l’absence d’accès au dossier personnel et indique la commande.

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
typographie française, tests unitaires, puis tests de l’interface et du site web (WebKit) sur un affichage Broadway virtuel
(jamais sur la session graphique de l’utilisateur).

Les tests qui touchent à GVfs vérifient les métadonnées réelles et se sautent si le service est absent.

## Traductions

Les textes sont en anglais dans le code et traduits avec gettext (`po/`). La langue française est complète.

## Capture d’écran

`python3 -m custicon --capture <écran> --out <fichier.png>` produit une image de l’interface à partir de données
de démonstration, sans ouvrir de fenêtre sur le bureau lorsqu’il est lancé sous Broadway. Ce mode est réservé au développement : `capture.py` n’est pas installé avec l’application. Les captures de
cette documentation sont produites ainsi.

## Sécurité

Custicon lit des fichiers qu’il ne maîtrise pas (pictogrammes choisis, thèmes d’icônes installés, registre
de données) et écrit une métadonnée dans les dossiers de l’utilisateur. Ce qui est garanti :

- **Le contenu des dossiers n’est jamais modifié.** Seul l’attribut `metadata::custom-icon` est écrit, par le
  service GVfs. Dans Flatpak, le dossier personnel est monté en lecture seule.
- **Aucun accès au réseau**, et aucune donnée envoyée. Le rendu SVG ne lit rien hors de son propre dossier.
- **Entrées bornées et refusées proprement** (voir « Composition ») : taille de fichier, dimensions des images,
  nombre d’éléments et profondeur des SVG, nombres non finis. Les scripts, objets étrangers et références
  externes d’un SVG sont retirés.
- **Isolation du pictogramme** : un pictogramme SVG est incorporé comme document séparé et ne peut pas modifier
  l’apparence du dossier.
- **Reprise après arrêt brutal** : le registre est écrit avant la métadonnée, avec retour arrière en cas
  d’échec ; l’icône d’origine de l’utilisateur n’est jamais perdue. Le registre est écrit de façon atomique,
  doublé d’une sauvegarde, et validé à la lecture.
- **Nettoyage prudent** : une image n’est supprimée que si aucun dossier ne la référence.

## Fonctions de la 1.4

- **Jeux de pictogrammes** : `IconSets` regroupe le thème, Phosphor (1 512, graisse normale), les émoji Fluent monochromes (1 595, noms français
  du CLDR) et les logos Simple Icons (3 467, trois exclus). Un nom enregistré porte son préfixe ; la recherche ignore les accents.
  `Settings.glyph_revision` marque les icônes générées avec une ancienne version d’un jeu, ce qui commande le bandeau de régénération.
- **Sélecteur** : grille virtualisée, catégories, barre latérale (suggestions, récents, favoris) masquée sur fenêtre étroite.
- **Éditeur** : deux colonnes dans un `Adw.BreakpointBin` (une seule colonne sous 600 sp). Le glisser (`Gtk.GestureDrag`) et la molette
  agissent sur le brouillon sans passer par les curseurs, qui suivent.
- **Plusieurs dossiers** : un lot (`MainWindow._batch`) se forme depuis le sélecteur de dossiers, le dépôt de fichiers ou le mode sélection
  de la liste. Il est protégé du rechargement automatique de la fenêtre.
- **Site web** : `data/website/`, page anglaise et page française, diaporama et visionneuse en JavaScript simple, redirection selon la langue
  du navigateur (`language.js`, choix mémorisé). `tests/test_website.py` vérifie les liens et rejoue ces comportements dans WebKit.

## Fonctions ajoutées après la 1.0

- **Couleur d’accent** : `Settings.accent` (exclusif avec une couleur choisie). L’accent est lu par le portail (`org.freedesktop.appearance`,
  `accent-color`), avec un bleu par défaut. `Service.outdated()` liste les dossiers dont le thème ou l’accent suivi a changé depuis
  la génération, ce qui commande le bandeau de régénération.
- **Pictogramme en texte** : le texte est converti en SVG vectoriel avant l’enregistrement, puis rangé comme une copie de pictogramme.
  La régénération réutilise cette copie et ne dépend donc pas de la police.
- **Styles et apparence mémorisée** : deux fichiers JSON du dossier de données, validés strictement à la lecture ; un fichier abîmé
  donne les valeurs d’origine sans erreur. L’export écrit directement le fichier choisi, car le sélecteur du bac à sable ne donne
  accès qu’à ce fichier.
- **Retrouver un dossier** : le registre est modifié avant la métadonnée, comme pour `apply`, avec retour arrière ; l’icône d’origine est conservée.
- **Ouverture depuis Nautilus** : `HANDLES_OPEN`, `MimeType=inode/directory` et `Exec=custicon %F`. Une seule instance, qui réutilise sa fenêtre.
