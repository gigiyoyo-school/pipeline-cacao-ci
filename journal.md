# Journal de bord du projet

Notes prises au fil de l'eau. Ce fichier n'est pas un livrable : c'est la matière première du rapport technique, en particulier des sections 2 (architecture, choix techniques justifiés) et 11 (difficultés rencontrées).

Règle : une entrée à chaque décision et à chaque blocage, le jour même.

---

## Étape 1 : génération du dataset

### Audit du générateur fourni

Le générateur de l'annexe A a été exécuté tel quel avant toute modification. Quatre incohérences relevées, chiffres à l'appui :

| Constat mesuré | Conséquence sur les analyses demandées |
|---|---|
| Les dates s'étalent du 01/01/2023 au 02/04/2041 | Analyse de saisonnalité mensuelle impossible |
| Prix moyen : Grade A 836 FCFA/kg, Hors grade 840 | L'analyse « prix moyen par qualité » n'a aucun sens |
| 320 couples région-coopérative pour 40 coopératives | La hiérarchie région / coopérative est fausse |
| Les 5 000 planteurs livrent dans les 8 régions | `dim_planteur` n'a aucun attribut stable |

Cause technique des deux premiers points : `pd.date_range(periods=80000, freq='2h')` couvre 18 ans, et `prix_fcfa_kg` est calculé à partir d'un second tirage de qualités indépendant de la colonne `qualite`.

### Décisions de modélisation

**Grain de la table de faits** : une ligne = une pesée. C'est le grain le plus fin disponible ; tout le reste s'obtient par agrégation. `id_pesee` est conservé comme dimension dégénérée.

**Étoile plutôt que flocon** : la table de faits porte directement les trois clés `id_region`, `id_cooperative`, `id_planteur`, malgré la hiérarchie naturelle entre les trois. La redondance est assumée en échange d'une jointure unique pour l'analyse par région. Contrepartie : rien n'interdit techniquement une incohérence entre la région de la pesée et celle de la coopérative, donc cette cohérence devient une règle de validation automatique du pipeline.

**Clés techniques** : identifiants entiers `SERIAL` dans les dimensions, code métier conservé en colonne. Motif : la clé reste stable si un planteur change de coopérative ou si une coopérative est renommée.

**`certifie_bio` déplacé dans `dim_planteur`** : la certification est une propriété de l'exploitation, pas de la livraison. Le générateur d'origine la tirait sur chaque ligne de pesée.

**Mesures et additivité** : `tonnage_kg` et `montant_fcfa` sont additifs, `prix_fcfa_kg` et `humidite_pct` ne le sont pas. Le prix moyen par qualité sera donc calculé en moyenne pondérée par le tonnage, `SUM(montant) / SUM(tonnage)`, et non en `AVG(prix)`.

### Corrections apportées au générateur

1. **Période** : deux campagnes complètes, du 01/10/2022 au 30/09/2024, avec pondération mensuelle réelle. La campagne principale (octobre à mars) représente 70,8 % du tonnage.
2. **Prix** : dérivé du grade réellement attribué, avec prime bio de 10 %, coefficient de 0,96 en campagne intermédiaire et prime de volume. Résultat : Grade A 1 006 FCFA/kg, Grade B 828, Grade C 675, Hors grade 500.

**Ajout après première vérification** : une prime de volume de 3 % au-delà de 500 kg et une décote de 3 % en dessous de 100 kg. Motif : sans corrélation entre prix et tonnage, la moyenne pondérée par le tonnage donnait exactement le même résultat que la moyenne simple, ce qui rendait la démonstration impossible. Justification métier : un gros lot coûte moins cher à manipuler et à transporter, la coopérative le paie donc un peu mieux. Écart obtenu après correction : 11 FCFA/kg sur le Grade A entre moyenne simple (1 006) et moyenne pondérée (1 017).
3. **Hiérarchie** : référentiels séparés pour les coopératives (40, rattachées à leur région) et les planteurs (5 000, rattachés à une coopérative). Vérifié : 40 couples région-coopérative, 0 planteur multi-région.
4. **Humidité dérivée de la qualité** : Grade A à 6,6 % en moyenne, Hors grade à 10,2 %, la norme d'exportation étant de 8 %.

### Défauts injectés volontairement

| Défaut | Volume | Situation réelle simulée |
|---|---|---|
| `humidite_pct` manquante | 3 152 lignes (4 %) | capteur défaillant, mesure oubliée |
| `prix_fcfa_kg` à -1 | 1 576 lignes (2 %) | code d'erreur du logiciel de bascule |
| Erreurs de saisie sur les noms | 2 364 lignes (3 %) | région ou coopérative tapée à la main |
| Doublons de scan | 1 200 lignes (1,5 %) | même ticket pesé deux fois |

Les tonnages saisis en tonnes au lieu de kilos ont été écartés : deux familles d'erreurs de saisie suffisent à démontrer la méthode.

### Limites à mentionner dans le rapport

Les données sont synthétiques. Les ordres de grandeur ont été calés sur la filière réelle (poids des régions, campagnes, barème de qualité, norme d'humidité de 8 %), mais le volume total de 24 750 tonnes sur deux ans représente un échantillon de pesées, pas la production nationale, qui dépasse 2 millions de tonnes par an.

---

## Étape 2 : extraction, audit, nettoyage et qualité

### Audit initial (script 02, aucune modification des données)

| Constat | Volume | Part |
|---|---|---|
| Humidité manquante | 3 198 lignes | 4,00 % |
| Prix à -1 (code d'erreur de la bascule) | 1 605 lignes | 2,01 % |
| Doublons sur `id_pesee` | 1 200 lignes | 1,50 % |
| Variantes de saisie sur `region` | 42 modalités brutes pour 8 réelles | 34 variantes |
| Variantes de saisie sur `cooperative` | 150 modalités brutes pour 40 réelles | 110 variantes |

Les doublons sont des copies strictement identiques : 1 200 doublons sur `id_pesee` et 1 200 lignes entièrement dupliquées. Il s'agit donc bien d'un double scan du même ticket, pas de deux pesées distinctes ayant reçu le même identifiant.

Aucune clé orpheline : après normalisation, tous les `id_planteur`, coopératives et régions des pesées existent dans les référentiels.

Période couverte : du 01/10/2022 au 30/09/2024, soit 730 jours et deux campagnes complètes.

### Décisions de nettoyage

**Normalisation des libellés avant tout le reste.** La méthode ne code aucune variante en dur : le libellé saisi et le libellé officiel sont réduits à la même forme canonique (sans accent, minuscules, ponctuation remplacée par des espaces), puis remplacés par l'officiel. Une faute de frappe jamais rencontrée sera rattrapée sans modifier le code. Résultat : 42 modalités de région ramenées à 8, 150 codes de coopérative ramenés à 40, zéro non résolu.

**Dédoublonnage avant les imputations.** Sinon les médianes seraient calculées sur des lignes comptées deux fois, et le volume total serait faussé.

**Imputation plutôt que suppression, avec marquage.** Une pesée dont le prix n'a pas été saisi reste une pesée dont le tonnage est valide : la supprimer ferait disparaître 2 % du tonnage des analyses de production. Les prix sont imputés par la médiane du même grade sur la même campagne (le prix garanti varie d'une campagne à l'autre), les humidités par la médiane du grade. Deux colonnes booléennes, `prix_impute` et `humidite_imputee`, permettent d'écarter ces lignes de toute analyse d'un simple filtre.

**Limite assumée** : l'humidité détermine le grade, donc l'imputer par la médiane du grade est circulaire. Les analyses portant spécifiquement sur l'humidité écarteront les 3 152 lignes concernées.

### Colonnes calculées (6, le barème en demande 4)

| Colonne | Contenu | Usage prévu |
|---|---|---|
| `campagne` | Principale (oct-mars) ou Intermédiaire | analyse de saisonnalité sans `CASE WHEN` |
| `saison` | 2022-2023 ou 2023-2024 | comparaison d'une campagne à l'autre |
| `conforme_export` | humidité ≤ 8 % | taux de conformité à la norme d'exportation |
| `categorie_tonnage` | Petit, Moyen, Gros, Très gros | distribution des apports |
| `montant_fcfa` | tonnage × prix | chiffre d'affaires et moyennes pondérées |
| `ecart_prix_grade_pct` | écart au prix médian du grade | détection des prix atypiques |

À quoi s'ajoutent les attributs récupérés des référentiels : `superficie_ha`, `annee_adhesion`, `certifie_bio`, `zone_production`, `port_export`.

### Résultats du contrôle qualité

7 règles écrites, 9 contrôles passés (deux règles se dédoublent sur plusieurs clés), 0 erreur, 0 avertissement sur 78 800 lignes.

| Règle | Objet |
|---|---|
| R1 | volume minimal de lignes |
| R2 | unicité de `id_pesee` |
| R3 | complétude des 8 colonnes critiques |
| R4 | domaines de valeurs (grades et régions connus) |
| R5 | bornes métier (tonnage, prix, humidité) |
| R6 | intégrité référentielle (planteurs et coopératives) |
| R7 | cohérence hiérarchique (région de la pesée = région du planteur) |

R7 est le garde-fou du choix d'étoile : la table de faits portera à la fois la région et la coopérative, rien n'interdit techniquement qu'elles se contredisent.

### Anomalies de pesée détectées (signalées, jamais supprimées)

| Anomalie | Critère | Lignes | Part |
|---|---|---|---|
| Tonnage extrême | > 1 246 kg (Q3 + 3 × IQR) | 563 | 0,71 % |
| Humidité hors norme export | > 8 % | 23 303 | 29,6 % |
| Prix atypique pour le grade | écart > 30 % à la médiane | 19 | 0,02 % |

Le point à défendre : détecter n'est pas supprimer. Un apport de 2 400 kg est statistiquement rare mais parfaitement possible pour une grande plantation, et près de 30 % du cacao au-dessus de la norme d'humidité est un constat métier à commenter, pas une erreur de données à effacer.

### Volumes

80 000 lignes extraites, 78 800 conservées après dédoublonnage, 9 colonnes en entrée, 25 en sortie.

---

## À faire

- [x] Confirmer auprès de l'enseignant le travail sans binôme
- [x] Créer le dépôt GitHub public et partager le lien
- [ ] Rédiger la déclaration d'usage des outils d'IA pour l'introduction du rapport
- [ ] Créer le projet Supabase pour l'étape 3