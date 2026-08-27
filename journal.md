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

## Étape 3 : schéma en étoile

### Convention de nommage

`id_xxx` désigne une clé technique entière générée par PostgreSQL, `code_xxx` le code métier issu de la source. La source appelle `id_planteur` un code au format `PLT00042` ; dans `dim_planteur` ce code devient `code_planteur`, et `id_planteur` désigne la clé `SERIAL`. Sans cette convention, les jointures deviennent ambiguës.

### Structure retenue

| Table | Lignes | Rôle |
|---|---|---|
| `dim_region` | 8 | zone de production, port d'exportation, nombre de coopératives et de planteurs |
| `dim_cooperative` | 40 | code, région de rattachement, année de création, certification, nombre d'adhérents |
| `dim_planteur` | 5 000 | coopérative et région de rattachement, superficie, année d'adhésion, certification bio |
| `dim_qualite` | 4 | rang, humidité maximale tolérée, bornes de prix, exportable ou non |
| `dim_date` | 731 | calendrier continu, avec campagne et saison précalculées |
| `faits_pesees` | 78 800 | 5 clés étrangères, 5 mesures, 4 attributs de traçabilité |

`dim_cooperative` porte sa région et `dim_planteur` porte sa coopérative et sa région. Ce n'est pas du flocon puisque la table de faits pointe directement vers les trois : c'est une dimension qui connaît son parent, ce qui permet de valider la cohérence côté base.

### Points à défendre en soutenance

**Pourquoi des clés `SERIAL` plutôt que les codes métier ?** Un entier se compare plus vite qu'une chaîne dans une jointure, et la clé reste stable si une coopérative est renommée ou si un planteur change d'affectation.

**Pourquoi `id_pesee` reste en clé primaire des faits ?** C'est une dimension dégénérée : un identifiant métier conservé dans la table de faits, sans dimension propre parce qu'il n'a aucun attribut à porter. Il assure la traçabilité jusqu'au ticket de pesée d'origine.

**Pourquoi la campagne est-elle dans `dim_date` et non calculée dans les requêtes ?** Parce qu'une règle métier écrite une fois dans la dimension ne peut pas diverger entre deux requêtes. Toute analyse de saisonnalité devient un `GROUP BY campagne`.

**Pourquoi des index sur les clés étrangères ?** PostgreSQL indexe automatiquement les clés primaires, jamais les clés étrangères. Sans ces index, chaque `JOIN` sur la table de faits impose un parcours complet des 78 800 lignes.

### Contrôles au chargement

Résolution des clés étrangères : 78 800 lignes sur 78 800, aucune clé non résolue. Deux contrôles supplémentaires côté base après chargement : jointure sur les cinq dimensions (le compte doit être identique à celui des faits) et vérification que la région de chaque pesée correspond à celle de sa coopérative.

### Volumes obtenus

24 345 tonnes de cacao, 20,73 milliards de FCFA de valeur d'achat sur deux campagnes.

---

## Étape 4 : requêtes analytiques

### Deux pièges rencontrés

**`ROUND` sur un flottant.** En PostgreSQL, `ROUND(valeur, decimales)` n'existe que pour le type `numeric`, pas pour `double precision`. Dès qu'une division produit un flottant, la requête échoue. Toutes les expressions arrondies sont donc converties explicitement : `ROUND((expression)::numeric, 1)`.

**Ordre chronologique d'une campagne.** Trier par saison puis par mois calendaire place janvier avant octobre à l'intérieur d'une même saison. La fonction `LAG` comparait donc chaque mois au mauvais mois précédent. Correction : une colonne `mois_campagne` dans `dim_date`, valant 1 pour octobre et 12 pour septembre. Le tri devient chronologiquement juste, et les graphiques de saisonnalité s'ordonnent naturellement.

### Les six requêtes

| Requête | Technique | Question métier |
|---|---|---|
| 1 | JOIN + GROUP BY + SUM() OVER () | production et prix par région |
| 2 | JOIN + FILTER | prix et conformité export par qualité |
| 3 | JOIN sur dim_date | saisonnalité mensuelle |
| 4 | Jointure quadruple | classement des coopératives |
| 5 | CTE + RANK + LAG + SUM() OVER | classement mensuel des régions et variation |
| 6 | JOIN + GROUP BY | effet de la certification bio |

### Résultats et lecture métier

**Production par région.** Soubré porte 21,1 % du tonnage, San Pedro 16,1 %, Bondoukou seulement 4,8 %. La concentration au Sud-Ouest correspond à la géographie réelle de la cacaoculture ivoirienne. Le prix moyen pondéré varie peu d'une région à l'autre, de 848 à 858 FCFA/kg : cohérent avec un prix garanti fixé nationalement, l'écart résiduel venant du mix de qualités et de la part de bio.

**Prix par qualité.** Grade A à 1 017 FCFA/kg en moyenne pondérée contre 1 006 en moyenne simple, soit 11 FCFA d'écart. La moyenne simple sous-estime le prix réellement payé, parce qu'elle donne le même poids à une pesée de 12 kg qu'à une pesée de 2 000 kg.

**Conformité à la norme d'exportation.** 99,8 % du Grade A respecte le seuil de 8 % d'humidité, contre 82,5 % du Grade B et 13,3 % du Grade C. C'est le constat le plus exploitable du projet : le Grade C est massivement hors norme, ce qui justifierait un plan de formation au séchage dans les coopératives concernées.

**Saisonnalité.** Pic à 2 209 tonnes en novembre, creux à 372 tonnes en août, soit un rapport de 1 à 6 entre le mois le plus fort et le plus faible. La part de Grade A passe de 38 % en campagne principale à 28 % en campagne intermédiaire : le cacao séché pendant l'harmattan est de meilleure qualité, et le prix moyen suit, de 875 à 795 FCFA/kg.

**Certification bio.** À grade égal, la prime est nette : 1 101 contre 1 003 FCFA/kg sur le Grade A, soit environ 10 %. La coopérative en tête du classement, COOP-SOU-003, affiche 41 % de planteurs certifiés et le meilleur prix moyen du classement, 870 FCFA/kg. La certification est le levier de revenu le plus visible du jeu de données.

### Choix d'outillage

Les requêtes sont définies une seule fois, dans `requetes.py`. Le script `06_requetes_sql.py` les exécute et régénère `sql/02_requetes_analytiques.sql` à partir de ce module : le fichier collé dans le SQL Editor ne peut pas diverger de celui qui tourne dans le pipeline.

Un mode `--local` exécute les mêmes requêtes sur les copies Parquet via DuckDB, qui comprend la syntaxe PostgreSQL utilisée ici. Cela permet de mettre au point une requête sans solliciter la base, et de travailler sans connexion.

---

## Étape 5 : tableau de bord

### Choix techniques

Le tableau de bord repart des résultats SQL archivés en Parquet à l'étape 4, jamais de la base : il se régénère hors connexion, et deux exécutions successives donnent exactement la même image.

Piège rencontré : PostgreSQL renvoie les colonnes `NUMERIC` sous forme d'objets `Decimal`. Pandas les stocke alors en type `object` et Matplotlib refuse de les tracer. Une fonction `numeriser()` convertit systématiquement les colonnes de mesure avant tracé. La conversion est sans effet quand les données viennent de DuckDB, qui renvoie des flottants.

Deux sorties : une planche complète de six graphiques, et un fichier par graphique dans `data/output/graphiques/`. Les fichiers individuels servent au rapport, où chaque graphique doit être commenté séparément, et aux slides. Résolution 150 dpi, comme exigé par la checklist.

### Interprétation des six graphiques

Un paragraphe par graphique, comme le demande le critère 3.

**1. Production par région.** Soubré porte 21,1 % du tonnage collecté, San Pedro 16,1 %, contre 4,8 % pour Bondoukou. Les trois premières régions représentent à elles seules plus de la moitié des apports. La distinction par port d'exportation révèle un enjeu logistique : les régions du Sud-Ouest et du Centre-Ouest, qui pèsent près de 62 % du tonnage, transitent toutes par San Pedro. Une saturation de ce port bloquerait la majorité de la filière, ce qui plaide pour un suivi séparé des flux par port.

**2. Saisonnalité des apports.** Le pic de novembre atteint 2 209 tonnes, le creux d'août 372 tonnes, soit un rapport de 1 à 6. Les six mois de campagne principale concentrent 70,8 % du tonnage annuel. Les deux saisons se superposent presque parfaitement, ce qui indique un rythme structurel et non un accident conjoncturel. Conséquence opérationnelle : le dimensionnement des équipes de pesée et des capacités de stockage doit se caler sur novembre, pas sur la moyenne annuelle.

**3. Évolution du prix moyen pondéré.** Le prix se maintient autour de 875 FCFA/kg pendant toute la campagne principale, puis décroche brutalement à environ 795 FCFA/kg dès avril, soit une baisse de 9 %. Deux causes se cumulent : le prix garanti est révisé à la baisse pour la campagne intermédiaire, et la qualité se dégrade, la part de Grade A passant de 38 % à 28 %. Le planteur qui peut stocker a donc intérêt à livrer pendant la campagne principale.

**4. Distribution des qualités et conformité export.** Le Grade B domine avec 9 505 tonnes, devant le Grade A à 8 594 tonnes. Le constat le plus actionnable du projet est le taux de conformité à la norme d'humidité de 8 % : 99,8 % pour le Grade A, 82,5 % pour le Grade B, mais seulement 13,3 % pour le Grade C. Près de 4 300 tonnes de Grade C sont donc invendables à l'export en l'état. Un programme de séchage ciblé sur les coopératives concernées transformerait une partie de ce volume en Grade B, avec un gain de 155 FCFA par kilo.

**5. Effet de la certification bio.** À grade égal, la prime est constante autour de 10 % : 1 101 contre 1 003 FCFA/kg sur le Grade A, 905 contre 825 sur le Grade B. Rapportée au tonnage certifié, cette prime représente plusieurs centaines de millions de FCFA sur les deux campagnes. C'est le levier de revenu le plus direct pour un planteur, et il ne dépend pas de la qualité du séchage.

**6. Classement des coopératives.** Les cinq premières coopératives sont toutes de Soubré, ce qui reflète la concentration régionale. COOP-SOU-003 se détache avec 1 172 tonnes et surtout 41 % de planteurs certifiés bio, contre 4 à 13 % pour les autres. Elle affiche aussi le meilleur prix moyen du classement, 870 FCFA/kg. Cette coopérative constitue un cas d'école à documenter : ce qu'elle fait pour atteindre ce taux de certification est reproductible ailleurs.

### Limite à mentionner

Les deux saisons se ressemblent beaucoup parce que le générateur leur applique la même loi saisonnière. Sur des données réelles, les aléas climatiques créeraient des écarts d'une campagne à l'autre. C'est une limite du jeu de données synthétique, à signaler dans la section 11 du rapport.

## À faire

- [x] Confirmer auprès de l'enseignant le travail sans binôme
- [x] Créer le dépôt GitHub public et partager le lien
- [ ] Rédiger la déclaration d'usage des outils d'IA pour l'introduction du rapport
- [x] Créer le projet Supabase
- [x] Capture d'écran du SQL Editor avec les 6 tables
- [x] Capture du Table Editor avec le nombre de lignes
- [x] Captures d'au moins 2 requêtes analytiques dans le SQL Editor
- [ ] Insérer les 6 graphiques commentés dans la section 9 du rapport