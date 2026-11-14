# Publier une version

Cette page décrit comment publier une version de Custicon sur GitHub. Elle s’adresse à la personne qui
maintient le dépôt.

## Ce qui est automatique

- À chaque push sur `main` et à chaque demande de fusion, le workflow **Check** lance `./check.sh`
  dans un conteneur Fedora 44.
- À l’envoi d’une étiquette `vX.Y.Z`, le workflow **Release** construit le paquet Flatpak
  `custicon-X.Y.Z.flatpak` (avec sa somme `sha256`) et le joint à une release **brouillon**.
  Il refuse de continuer si le nom de l’étiquette n’a pas la forme `vX.Y.Z`, si elle ne pointe pas vers un
  commit de la branche `main`, ou si elle ne correspond pas aux numéros de version du dépôt.
- Le workflow est séparé en deux tâches : la **construction** n’a qu’un droit de lecture sur le dépôt,
  seule la tâche **release** peut écrire (créer le brouillon et y joindre les fichiers).
- Une release **déjà publiée n’est jamais modifiée** : le workflow s’arrête avec un message.
- Les actions GitHub utilisées sont épinglées par empreinte de commit (checkout 7.0.1, upload-artifact 7.0.2,
  download-artifact 7.0.0). Pour les mettre à jour, remplacer l’empreinte après avoir vérifié la nouvelle version.

- À la **publication** d’une release, le workflow **Flatpak repository** télécharge le paquet de la dernière release, vérifie sa somme `sha256`,
  l’importe dans un dépôt Flatpak neuf, le **signe** avec la clé du secret `FLATPAK_REPO_SIGNING_KEY` et le publie sur GitHub Pages,
  à l’adresse `https://gor3pig.github.io/Custicon/repo/`. Les copies installées depuis `custicon.flatpakref` proposent alors la mise à jour.
  Il peut aussi être lancé à la main (onglet **Actions**, depuis `main`) pour republier le dépôt à partir de la dernière release.

Pages sert le site (`data/website/`, en anglais et en français), le dépôt signé (`repo/`), `custicon.flatpakref`, `custicon.flatpakrepo`, `custicon.gpg` et l’icône.
Un changement sous `data/website/` relance aussi la publication.

## Le dépôt Flatpak et sa clé

- Le dépôt ne contient que la **dernière release** (`build-aux/flatpak-repo.sh`).
- Les fichiers `build-aux/custicon.flatpakref` et `build-aux/custicon.flatpakrepo` portent l’adresse du dépôt et la **clé publique** (base64) ;
  `build-aux/custicon.gpg` est cette clé publique. L’étape « Flatpak repository files » de `./check.sh` vérifie qu’ils sont cohérents entre eux,
  avec `release.yml` (le paquet intègre l’adresse du dépôt et la clé) et avec l’empreinte `SIGNING_KEY_ID` de `pages.yml`.
- La **clé privée** n’existe que dans le secret GitHub `FLATPAK_REPO_SIGNING_KEY` et dans une sauvegarde hors ligne du mainteneur.
  Si elle est perdue ou volée : créer une nouvelle clé, mettre à jour le secret, `custicon.gpg`, les deux fichiers de référence et `SIGNING_KEY_ID`,
  et demander aux utilisateurs de réinstaller Custicon depuis le nouveau `.flatpakref`.
- L’environnement `github-pages` n’accepte les déploiements que depuis `main` et les étiquettes `v*`.

## Publier une version

1. **Mettre à jour les versions** dans `meson.build`, `src/custicon/config.py` et dans la première entrée
   `<release>` de `data/io.github.gor3pig.Custicon.metainfo.xml.in`, avec ses notes en anglais.
   Régénérer `po/custicon.pot`, puis traduire les nouveaux textes dans `po/fr.po`.
2. **Contrôler** avec `./check.sh`. L’étape « Versions match » vérifie que les trois numéros sont identiques.
3. **Tester le paquet** avant d’étiqueter (voir plus bas, « Construire un paquet de test »).
4. **Valider le commit** (message en anglais) avec le titre `Release version X.Y.Z`, puis l’envoyer sur `main`.
5. **Étiqueter** ce commit avec `vX.Y.Z` et envoyer l’étiquette.
6. Le workflow **Release** crée la release **brouillon** et y joint le paquet. Y rédiger les notes
   (voir « Notes de release ») puis **publier**.
7. La publication lance le workflow **Flatpak repository**, qui met le dépôt signé à jour. Vérifier qu’il est vert,
   puis que `flatpak update` propose la nouvelle version.

## Construire un paquet de test

Dans l’onglet **Actions** de GitHub, lancer le workflow **Release** à la main **en laissant le champ `tag` vide**.
Il construit le paquet à partir de la branche choisie, sans créer de release, et le garde comme artefact
pendant 14 jours (artefact `custicon-package`). Le télécharger, l’installer et l’essayer.

## Reconstruire le paquet d’une release

Lancer le workflow **Release** à la main en indiquant l’étiquette existante (par exemple `v1.0.0`).
Si la release est encore un **brouillon**, son paquet est reconstruit et remplacé. Si elle est **publiée**,
le workflow refuse : publier une version corrective à la place.

## Notes de release

Les notes sont écrites en **anglais puis en français**, avec le même contenu dans les deux langues :

1. Un court résumé de la version.
2. Les changements, regroupés sous des titres comme *New*, *Improved* et *Fixed* (en français : *Nouveau*, *Amélioré*, *Corrigé*).
3. **Install** (*Installation*) : ouvrir `custicon.flatpakref` ou `flatpak install --user https://gor3pig.github.io/Custicon/custicon.flatpakref` ;
   le fichier `.flatpak` (avec sa somme `sha256`) reste une solution de repli. **Update** (*Mise à jour*) : les copies installées depuis le dépôt se mettent à jour toutes seules.
4. Les limites connues (par exemple : dossiers du dossier personnel seulement, F5 nécessaire dans Nautilus).
5. Un lien vers le [guide d’utilisation](../help/fr/README.md) et [son équivalent anglais](../help/en/README.md).

## Si une étiquette est fausse

Supprimer l’étiquette et la release brouillon sur GitHub, corriger, puis étiqueter de nouveau.
Une release déjà publiée ne se corrige pas : publier une version corrective (`X.Y.Z+1`).
