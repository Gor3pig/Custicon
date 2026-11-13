# Guide d’utilisation de Custicon

Custicon pose un pictogramme sur l’icône de dossier de votre thème, pour les dossiers de votre choix.
Ce guide décrit chaque étape.

<img src="images/fenetre-principale.png" alt="La fenêtre principale de Custicon" width="800">

## Sommaire

- [Premiers pas](#premiers-pas)
- [Choisir le pictogramme](#choisir-le-pictogramme)
- [Régler l’apparence](#régler-lapparence)
- [Appliquer et voir le résultat](#appliquer-et-voir-le-résultat)
- [Gérer ses dossiers](#gérer-ses-dossiers)
- [Retirer une icône](#retirer-une-icône)
- [Après un changement de thème](#après-un-changement-de-thème)
- [Raccourcis clavier](#raccourcis-clavier)
- [Questions et réponses](#questions-et-réponses)

## Premiers pas

Au premier lancement, la fenêtre propose de choisir un dossier. Vous pouvez aussi utiliser
**Ajouter un dossier** en haut à gauche (Ctrl+O).

1. Choisissez le dossier à personnaliser. Il doit se trouver dans votre dossier personnel.
2. Le dossier apparaît dans la liste de gauche, avec l’icône de dossier de votre thème.
3. L’éditeur s’ouvre à droite. Le bouton **Appliquer** reste grisé tant qu’aucun pictogramme n’est choisi.

> [!NOTE]
> Choisir un dossier ne modifie rien. Rien n’est enregistré tant que vous n’avez pas appuyé sur **Appliquer**.

## Choisir le pictogramme

Deux possibilités, dans le groupe **Pictogramme**.

### Une icône du système

**Icône du système** > **Choisir…** ouvre la liste des pictogrammes de votre thème.

<img src="images/choix-pictogramme.png" alt="Le choix du pictogramme" width="800">

- Tapez un mot dans le champ de recherche. Les noms sont ceux du thème, en anglais
  (par exemple `camera`, `music`, `folder`, `star`).
- Cliquez sur un pictogramme puis sur **Choisir**, ou double-cliquez dessus.
- Si le thème actif ne fournit pas l’icône demandée, Custicon utilise celle d’Adwaita.

### Un fichier

**Fichier SVG ou PNG** > **Parcourir…** permet de prendre votre propre image.

- **Teinter le pictogramme** applique la couleur choisie à l’image. Pour un SVG, les couleurs
  d’origine sont remplacées ; pour un PNG, seule la forme (la transparence) est conservée.
- Désactivez la teinte pour garder les couleurs d’origine de l’image.

## Régler l’apparence

Le groupe **Apparence** règle le rendu. L’aperçu en haut de l’éditeur montre le résultat
à quatre tailles, avec l’icône de dossier de votre thème actif.

- **Couleur automatique** : Custicon prend la couleur de la face du dossier et en déduit une teinte
  plus foncée, comme le font les dossiers Documents ou Musique. Désactivez-la pour choisir la couleur
  vous-même.
- **Taille** : de 20 % à 300 % de la taille d’origine.
- **Position horizontale** et **Position verticale** : de -50 % à +50 %, depuis le centre de la face du dossier.
- **Rétablir les valeurs par défaut** remet la couleur automatique, la taille et la position d’origine.

## Appliquer et voir le résultat

Appuyez sur **Appliquer**. Un message confirme l’opération.

**Nautilus n’actualise pas ses icônes tout seul.** Appuyez sur **F5** dans la fenêtre de Nautilus
pour voir le changement. Le même geste est nécessaire après un retrait ou une régénération.

## Gérer ses dossiers

La liste de gauche montre tous les dossiers personnalisés, par ordre alphabétique. Chaque dossier peut
afficher une alerte :

<img src="images/etats-particuliers.png" alt="Des dossiers dans un état particulier" width="800">

| Alerte | Signification | Que faire |
|---|---|---|
| Dossier introuvable | Le dossier a été supprimé ou déplacé. | **Oublier ce dossier**. |
| Icône modifiée depuis Nautilus | Vous avez choisi une autre icône dans Nautilus. Custicon n’y touche plus. | **Reprendre l’icône Custicon**, ou **Oublier ce dossier**. |
| Fichier d’icône manquant | L’image générée a disparu. | **Régénérer l’icône**. |
| Métadonnées illisibles | Les métadonnées du dossier ne peuvent pas être lues. | Réessayer ou oublier le dossier. |

Quand la fenêtre redevient active, la liste se met à jour. Vos réglages en cours ne sont pas perdus.

## Retirer une icône

**Retirer l’icône** demande une confirmation. Le dossier retrouve l’icône qu’il avait avant Custicon :
aucune icône personnalisée, ou celle que vous aviez choisie vous-même dans Nautilus.
Le contenu du dossier n’est jamais touché.

**Oublier ce dossier** (pour un dossier introuvable ou dont vous avez changé l’icône) arrête simplement
le suivi, sans modifier l’icône actuelle.

## Après un changement de thème

Quand vous changez de thème d’icônes, les icônes déjà posées gardent l’ancien dossier.
**Régénérer toutes les icônes** (bouton en bas de la liste, ou Ctrl+R) les recalcule avec le nouveau thème,
en gardant vos réglages. Un compte rendu indique ce qui a été mis à jour, ignoré ou introuvable.
Les dossiers dont vous avez changé l’icône depuis Nautilus sont ignorés.

Appuyez ensuite sur F5 dans Nautilus.

## Raccourcis clavier

| Raccourci | Action |
|---|---|
| Ctrl+O | Ajouter un dossier |
| Ctrl+R | Régénérer toutes les icônes |
| Ctrl+Q | Quitter |

## Questions et réponses

**Pourquoi dois-je appuyer sur F5 ?**
Nautilus lit les icônes personnalisées à l’ouverture d’un dossier et ne surveille pas leurs changements.
F5 recharge le contenu de la fenêtre.

**L’icône du dossier n’est pas celle de mon thème.**
Custicon utilise l’icône `folder` de votre thème actif, en suivant l’héritage entre thèmes.
Si votre thème ne fournit qu’un PNG, le résultat est en bitmap et peut paraître moins net à grande taille.

**Où sont rangées les images générées ?**
Dans l’espace de données de l’application (`~/.var/app/io.github.gor3pig.Custicon/data/custicon/`).
Une désinstallation avec suppression des données les efface : retirez d’abord les icônes.

**Pourquoi ne peut-on choisir que les dossiers du dossier personnel ?**
Pour que Nautilus voie l’icône, Custicon doit écrire sur le vrai chemin du dossier. Les disques
externes ne sont pas pris en charge dans cette version.

**Mon dossier est choisi mais l’icône n’apparaît pas dans Nautilus.**
Appuyez sur F5. Si rien ne change, regardez si le dossier affiche une alerte dans la liste de Custicon.
