# Publier une version

Cette page décrit comment publier une version de Custicon sur GitHub. Elle s’adresse à la personne qui
maintient le dépôt.

## Ce qui est automatique

- À chaque push sur `main` et à chaque demande de fusion, le workflow **Check** lance `./check.sh`
  dans un conteneur Fedora 44.
- À l’envoi d’une étiquette `vX.Y.Z`, le workflow **Release** construit le paquet Flatpak
  `custicon-X.Y.Z.flatpak` (avec sa somme `sha256`) et le joint à une release **brouillon**.
  Il refuse de continuer si l’étiquette ne correspond pas aux numéros de version du dépôt.

Il n’y a pas de dépôt Flatpak signé ni de page web : on installe directement le fichier `.flatpak` joint à la release.

## Publier une version

1. **Mettre à jour les versions** dans `meson.build`, `src/custicon/config.py` et dans la première entrée
   `<release>` de `data/io.github.gor3pig.Custicon.metainfo.xml.in`, avec ses notes en anglais.
   Régénérer `po/custicon.pot`, puis traduire les nouveaux textes dans `po/fr.po`.
2. **Contrôler** avec `./check.sh`. L’étape « Versions match » vérifie que les trois numéros sont identiques.
3. **Tester le paquet** avant d’étiqueter (voir plus bas, « Construire un paquet de test »).
4. **Valider le commit** avec le titre `Release version X.Y.Z`, puis l’envoyer sur `main`.
5. **Étiqueter** ce commit avec `vX.Y.Z` et envoyer l’étiquette.
6. Le workflow **Release** crée la release **brouillon** et y joint le paquet. Y rédiger les notes
   (voir « Notes de release ») puis **publier**.

## Construire un paquet de test

Dans l’onglet **Actions** de GitHub, lancer le workflow **Release** à la main **en laissant le champ `tag` vide**.
Il construit le paquet à partir de la branche choisie, sans créer de release, et le garde comme artefact
pendant 14 jours (`custicon-test-package`). Le télécharger, l’installer et l’essayer.

## Reconstruire le paquet d’une release

Lancer le workflow **Release** à la main en indiquant l’étiquette existante (par exemple `v1.0.0`).
Le paquet de la release est reconstruit et remplacé.

## Notes de release

Les notes sont écrites en **anglais puis en français**, avec le même contenu dans les deux langues :

1. Un court résumé de la version.
2. Les changements, regroupés sous des titres comme *New*, *Improved* et *Fixed* (en français : *Nouveau*, *Amélioré*, *Corrigé*).
3. **Install** (*Installation*) : télécharger le fichier `.flatpak`, vérifier la somme `sha256`, puis
   `flatpak install --user custicon-X.Y.Z.flatpak`.
4. Les limites connues (par exemple : dossiers du dossier personnel seulement, F5 nécessaire dans Nautilus).
5. Un lien vers le [guide d’utilisation](guide.md).

## Si une étiquette est fausse

Supprimer l’étiquette et la release brouillon sur GitHub, corriger, puis étiqueter de nouveau.
Une release déjà publiée ne se corrige pas : publier une version corrective (`X.Y.Z+1`).
