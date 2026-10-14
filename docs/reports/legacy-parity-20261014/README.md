# Legacy parity: first eleven resources

14 October 2026 · pypath `c5b4eb660` (CellChat: with the fix below, uncommitted) · build cache on nicesrv
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
| **CellPhoneDB** | – | – | – | – | – | **v2 does not read a file legacy needs** |

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
4. **CellPhoneDB: protein roles are not imported.** `inputs_v2/cellphonedb.py`
   defines `download_proteins` (`protein_input.csv`) but no dataset uses it, and
   the file is not in the build cache. Legacy reads it for the receptor,
   secreted, transmembrane, peripheral and integrin flags, which give
   CellPhoneDB interactions their ligand and receptor sides and feed the
   intercell annotations. The pairs themselves could not be compared for the
   same reason.

### Not comparable on the same file

The legacy and v2 inputs of SIGNOR interactions (CSV export vs causalTab),
ComplexPortal (PSI-MI XML vs ComplexTAB) and Matrisome (the rescued 2014 XLS vs
the current Google Sheet) read different files. These need the served-level
comparison against the legacy web service instead.

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
