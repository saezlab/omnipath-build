# Legacy parity: first eleven resources

14 October 2026 · pypath `c5b4eb660`, CellChat and CellPhoneDB with the fixes below · build cache on nicesrv
(CellChat and CellPhoneDB compared on the laptop, on copies of the same files; the CellPhoneDB proteins file is a new input, fetched once)
(`/root/data/pypath-cache`) · harness [`scripts/legacy_parity.py`](../../../scripts/legacy_parity.py)

## What was compared

For each resource, the legacy pypath input (`pypath.inputs`) and its inputs_v2
port read **the same cached file** and are reduced to keys in the source's own
identifiers:

- interactions: the (subject, object) pair, and the sign where both sides give one;
- complexes: the member set (SIGNOR: the complex ID, then its members).

Neither side translates identifiers, so a difference is a difference in
parsing, not in ID mapping or in the upstream release. Nothing was downloaded:
a legacy input that asks for a file the inputs_v2 module does not read stops
with `NotCached`. The per-resource JSON files in this folder have the counts,
up to 25 examples of each kind of difference, and the files each side read
with their checksums.

## Results

| Resource | Legacy keys | Found in v2 | Only legacy | Only v2 | Signs agree | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| TRRUST | 8,427 pairs | 8,427 | 0 | 0 | 4,859 / 4,859 | identical |
| DoRothEA (A–E) | 1,019,144 pairs | 1,019,144 | 0 | 0 | 5,702 / 5,702 | identical |
| SignaLink | 21,259 pairs | 21,259 | 0 | 0 | 1,669 / 1,669 | identical |
| CollecTRI | 43,536 pairs | 43,416 | 120 | 120 | 19,587 / 19,587 | intended difference |
| PhosphoSite | 7,184 pairs | 7,183 | 1 | 70 | (legacy has no sign) | v2 better; explained |
| CORUM | 2,734 complexes | 2,733 | 1 | 1 | – | v2 better |
| SIGNOR complexes | 681 IDs | 681 | 0 | 9 | – | v2 better |
| CellChat | 3,233 pairs | 3,229 | 4 | 0 | – | fixed (see below); explained |
| SIGNOR interactions | – | – | – | – | – | not the same file |
| ComplexPortal | – | – | – | – | – | not the same file |
| Matrisome | – | – | – | – | – | not the same file |
| CellPhoneDB roles | 2,533 (entity, role) | 2,533 | 0 | 0 | – | added (see below); identical |
| CellPhoneDB interactions | – | – | – | – | – | legacy translates IDs (UniProt tables) |

### Explained differences

- **CollecTRI** (120): miRNA targets. Legacy renames the gene symbol `MIR223` to
  the mature miRNA `hsa-miR-223`; v2 keeps the gene symbol. A transcription
  factor regulates the gene, so v2's choice is defensible. Worth a decision.
- **PhosphoSite**: the one legacy-only pair is a legacy bug: it cuts the RefSeq
  substrate `NP_001184222` (CRMP-2 iso3) down to `NP`. The 70 v2-only relations
  have hamster or frog substrates, which v2 deliberately leaves without a taxon,
  so the human filter of the comparison kept them.
- **CORUM** (1): the source writes `Q9NZL9-` with a trailing hyphen; v2 reads
  `Q9NZL9`, legacy keeps the broken accession.
- **SIGNOR complexes** (9 only in v2): legacy keys complexes by member set, so of
  two SIGNOR IDs with the same members the later overwrites the earlier, e.g.
  SMAD1/5/8/SMAD4 (SIGNOR-C215) replaces SMAD1/4 (SIGNOR-C85), and Fibrin
  (C317) replaces Fibrinogen (C311). v2 keeps every ID. The 632 complexes with
  plain members on both sides have identical member sets; 49 with nested
  complexes or families were not compared member by member (legacy expands them
  into every combination, v2 keeps the reference).
- **DoRothEA signs**: v2 gives no direction where `mor` is 0, which is most of
  the table; that is the intended reading.
- **SignaLink signs**: a row with both "dephosphorylation" and "stimulation"
  yields a decreased-phosphorylation relation and an increased-activity
  relation in v2. Only the activity direction is a regulatory sign; counted
  that way, all signs agree.

### Findings

1. **CellChat: metabolite ligands were proteins (fixed).** CellChatDB names a
   metabolite ligand by its abbreviation followed by the genes it uses as a
   proxy for the metabolite, the enzymes that make it and the transporters that
   release it (`PGE2-PTGES`, `5-HT-TPH1_SLC18A1`, `Glu-SLC17A7_GLS`), and
   annotates these rows "Non-protein Signaling". v2 built the ligand from those
   genes: a single protein (140 human interactions, e.g. "PTGES interacts with
   PTGER1") or a protein complex of enzyme and transporter (848). That is 988 of
   3,233 human interactions (31 %), and 995 in mouse; the complex datasets
   also held these gene sets as 111 complexes per species. The parity check
   first saw only the 140, because the complexes kept CellChat's label.

   The fix (`pypath/inputs_v2/cellchat.py`): the ligand of those rows is the
   metabolite, a `ChemicalEntity` with its ChEBI term, name and CellChat's
   abbreviation, from a table of the 43 abbreviations CellChat uses. Each ChEBI
   term was looked up by name or synonym in the cached ChEBI release; where the
   name covers stereoisomers, the table has the one the enzyme makes
   ((R)-adrenaline, 17beta-estradiol, all-trans-retinoic acid, L-glutamic acid).
   Four abbreviations do not tell the metabolite and keep it as the name only,
   unresolved: `Cort` (cortisol in human, corticosterone in rodents), `22HC`,
   `5alphaP` and `5-HT-DOPA` (DDC, with serotonin and dopamine receptors): 27
   human interactions. The proxy genes are no longer complexes; new datasets
   `{human,mouse,zebrafish}_metabolite_genes` associate each gene with its
   metabolite once (114 human, 123 mouse relations). Peptide and protein
   hormones annotated the same way (β-endorphin, TSH, PNOC) stay proteins.
   Result: 988 human interactions with a metabolite ligand, 961 of them with a
   ChEBI term. Tests in `packages/build/tests/test_mapping_review_fixes.py`.
2. **CellChat: source errors in the proxy genes.** `DHEA-STS` has `UBASH3B`
   (whose alias is STS) instead of the steroid sulfatase, and `DHEAS-SULT2B` has
   `SULT1A1`; the row with ligand `CXCL8` and receptor `CXCR1` has `CXCL1` as its
   symbol. v2 follows the source. These are the 4 remaining legacy-only pairs
   with β-endorphin, which legacy names by the label and v2 as POMC.
3. **CellChat zebrafish: 54 ligands without genes.** Complexes such as
   "Inhibin A" or "IL23 complex" are used in the zebrafish interactions but not
   defined in its complex table, so they become untyped named things. Not
   fixed; separate from the above.
4. **CellPhoneDB: protein roles were not imported (fixed).**
   `inputs_v2/cellphonedb.py` defined `download_proteins` (`protein_input.csv`)
   but no dataset used it, and it ignored the same flags in `complex_input.csv`.
   Legacy reads them for the receptor, secreted, transmembrane, peripheral and
   integrin roles, which give CellPhoneDB interactions their ligand and receptor
   sides and feed the intercell annotations. New datasets: `role_terms` (the
   five roles and the receptor and secreted classes such as "cytokine" or
   "tnf_receptor", each below its role, in a namespace per role because one
   class name is used for both), `protein_roles` and `complex_roles` (each
   protein and complex associated with its classes, or with the role where it
   has none). Compared with legacy's own reading of the flags, by the source's
   names: 2,533 (entity, role) pairs on both sides, none on one side only.
   The interactions themselves still cannot be compared: legacy translates
   gene symbols and HLA names to UniProt with its mapping tables.
5. **CellPhoneDB: metabolite ligands had no ChEBI term (fixed).** CellPhoneDB
   writes a metabolite ligand as a "system" (`Cholesterol_byLIPA`,
   `Glutamate_byGLS_and_SLC1A1`); v2 already made it a chemical entity, by name
   only, so none resolved. `complex_input.csv` gives the ChEBI term of about
   half of them in `secreted_desc`; each was checked against the name in ChEBI.
   v2 now takes that term (for interactions through the complex table), and a
   table of verified terms for the metabolites written without one. Two
   systems of two metabolites (`AldosteroneCorticosterone`,
   `SerotoninDopamin`, whose term is serotonin's) and `Progesterone_byHSD3B1`
   (three terms) keep the name only, as does β-endorphin. 48 of the 52
   metabolite partners of the interactions now have a ChEBI term.

### Not comparable on the same file

The legacy and v2 inputs of SIGNOR interactions (CSV export vs causalTab),
ComplexPortal (PSI-MI XML vs ComplexTAB) and Matrisome (the rescued 2014 XLS vs
the current Google Sheet) read different files. These need the served-level
comparison against the legacy web service instead.

## Second batch: thirteen more resources

Same method; the results are the JSON files of the same names. A legacy input
that downloads with `requests` or `urllib` instead of `Curl` (KEA did) is now
stopped as well.

| Resource | Legacy pairs | Found in v2 | Only legacy | Only v2 | Signs agree | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| ARN | 95 | 95 | 0 | 0 | 28 / 28 | identical |
| NRF2ome | 109 | 109 | 0 | 0 | 41 / 41 | identical |
| Li2012 | 185 | 185 | 0 | 0 | – | identical |
| PhosphoNetworks | 1,821 | 1,821 | 0 | 0 | – | identical |
| PhosphoPoint | 9,269 | 9,269 | 0 | 0 | – | identical |
| Ramilowski2015 | 1,894 | 1,894 | 0 | 0 | – | identical |
| LRdb | 3,239 | 3,239 | 0 | 0 | – | identical |
| CellTalkDB | 3,398 | 3,398 | 0 | 0 | – | identical |
| HPRD (enzyme-substrate) | 4,264 | 4,256 | 8 | 5 | – | nearly identical |
| dbPTM | 1,107 | 1,100 | 7 | 1 | – | v2 better |
| CA1 | 1,259 | 1,056 | 203 | 0 | 551 / 553 | intended gap; option |
| **Macrophage** | 1,702 | 174 | 1,528 | 222 | – | **v2 drops complexes** |
| Guide to Pharmacology | 742 | 120 | 622 | – | 109 / 109 | identifiers spelled differently |

- **HPRD**: legacy keeps the in vivo modifications by default; compared with
  the in vitro ones included, as v2 has them. 8 pairs are left, a few
  transglutaminase and amidation records whose enzyme the two sides name
  differently; not traced further.
- **dbPTM**: legacy keeps enzyme cells such as `MAPK1 OR MAPK3` as one name;
  v2 splits them.
- **CA1**: components without an accession (`CALCIUM`, `PIP2`, `IP3`, `cAMP`,
  `cGMP`, `SEROTONIN`, `GLYCINE`, `GLUTAMATE`, ions, DNA elements) are not
  emitted by v2, on purpose: the table has no identifier for them. Legacy keeps
  the names, but legacy OmniPath could not map them either. The table's
  Function column says what they are (Messenger, Lipid, Ligand, Ion), so about
  250 links with small molecules could be added with a table of ChEBI terms, as
  for CellChat.
- **Macrophage**: 1,075 of the 2,024 rows of the sheet have a complex on one
  side or both. Legacy splits a complex into its genes and pairs each with the
  other partner; v2 emits no relation for these rows. The rows of two proteins
  are mapped correctly (591 of 690; the others name no gene, e.g. `MAP3K?`, or
  state no relation). The complex cells list their members' symbols and Gene IDs
  (`ATM:CHUK:IKBKB:IKBKG`, `472:1147:3551:4214:23085`; the counts do not always
  agree), so v2 could make them complexes of their members.
- **Guide to Pharmacology**: legacy names a peptide ligand by its gene symbol
  (`TNFSF9`), v2 by its GtoP ligand, UniProt and Ensembl identifiers (P41273):
  the pair is there (checked for TNFSF9 and its receptor Q07011), but the
  comparison cannot see it. Legacy also keeps only human targets.

Not comparable on the same file: DIP (legacy reads another release), SPIKE
(legacy translates to UniProt), phospho.ELM, MIMP, iPTMnet and HPMR (legacy
reads further files). KEA and ICELLNET: the legacy function fails inside
(`generator raised StopIteration`).

## How far this reaches

Of the 173 inputs_v2 modules, 99 read at least one file under a URL that a
legacy input also uses; those can get the same check. Most of the others have
no legacy counterpart (ontologies, ChEBI, ChEMBL, the metabolomics resources).
Each resource needs a short adapter in the harness (5–10 lines), because the
legacy functions return different structures.

## Notes on the harness

- The legacy package does not import cleanly any more: module-level lookups
  call UniChem, miRBase and Ensembl, whose formats changed. The harness stubs
  them, and serves organism names for human, mouse and rat from a small table.
- `pypath.inputs.rdata` (legacy) patches the `rdata` converter on import, after
  which the v2 CellChat parser fails. Each resource therefore runs in its own
  process, v2 before legacy.
