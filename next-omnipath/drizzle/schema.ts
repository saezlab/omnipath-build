import { pgTable, pgSchema, varchar, json, integer, timestamp, boolean, bigint, doublePrecision } from "drizzle-orm/pg-core"
import { sql } from "drizzle-orm"

export const metadata = pgSchema("metadata");
export const silver = pgSchema("silver");
export const bronze = pgSchema("bronze");
export const gold = pgSchema("gold");


export const datasetsInMetadata = metadata.table("datasets", {
	resourceId: varchar("resource_id"),
	name: varchar(),
	entityType: varchar("entity_type"),
	category: varchar(),
	types: json(),
	evidenceLevel: varchar("evidence_level"),
	taxonScope: varchar("taxon_scope"),
	download: json(),
	dataProcessing: json("data_processing"),
});

export const resourcesInMetadata = metadata.table("resources", {
	id: varchar(),
	name: varchar(),
	description: varchar(),
	license: varchar(),
	primaryPubmed: varchar("primary_pubmed"),
	health: varchar(),
	website: varchar(),
	updateCategory: varchar("update_category"),
	accessCategory: varchar("access_category"),
});

export const transformationFunctionsInMetadata = metadata.table("transformation_functions", {
	id: integer(),
	name: varchar(),
	description: varchar(),
	category: varchar(),
	sqlDefinition: varchar("sql_definition"),
	argumentSchema: json("argument_schema"),
	createdAt: timestamp("created_at", { mode: 'string' }),
	updatedAt: timestamp("updated_at", { mode: 'string' }),
});

export const cvTermInSilver = silver.table("cv_term", {
	sourceDatabase: varchar("source_database"),
	namespace: varchar(),
	accession: varchar(),
	name: varchar(),
	definition: varchar(),
	isObsolete: boolean("is_obsolete"),
	replacedByAccession: varchar("replaced_by_accession"),
	categoryAccession: varchar("category_accession"),
	isA: varchar("is_a"),
	comment: varchar(),
	synonyms: varchar(),
	references: varchar(),
});

export const entitiesInSilver = silver.table("entities", {
	sourceDatabase: varchar("source_database"),
	canonicalIdentifier: varchar("canonical_identifier"),
	canonicalIdentifierType: varchar("canonical_identifier_type"),
	entityType: varchar("entity_type"),
	description: varchar(),
	ncbiTaxId: varchar("ncbi_tax_id"),
	cvTerms: varchar("cv_terms"),
	altId: varchar("alt_id"),
	members: varchar(),
	sequence: varchar(),
	length: integer(),
	mass: integer(),
});

export const interactionsInSilver = silver.table("interactions", {
	sourceDatabase: varchar("source_database"),
	sourceIdentifier: varchar("source_identifier"),
	dataSource: varchar("data_source"),
	entityA: varchar("entity_a"),
	entityB: varchar("entity_b"),
	entityAIdType: varchar("entity_a_id_type"),
	entityBIdType: varchar("entity_b_id_type"),
	entityAType: varchar("entity_a_type"),
	entityBType: varchar("entity_b_type"),
	pubmedId: varchar("pubmed_id"),
	interactionType: varchar("interaction_type"),
	detectionMethods: varchar("detection_methods"),
	evidenceSentence: varchar("evidence_sentence"),
	causalMechanism: varchar("causal_mechanism"),
	causalStatement: varchar("causal_statement"),
});

export const iptmnetInteractionsInBronze = bronze.table("iptmnet__interactions", {
	column0: varchar(),
	column1: varchar(),
	column2: varchar(),
	column3: varchar(),
	column4: varchar(),
	column5: varchar(),
	column6: varchar(),
	column7: varchar(),
	column8: varchar(),
	column9: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const iptmnetIptmnetInteractionsInBronze = bronze.table("iptmnet__iptmnet_interactions", {
	column0: varchar(),
	column1: varchar(),
	column2: varchar(),
	column3: varchar(),
	column4: varchar(),
	column5: varchar(),
	column6: varchar(),
	column7: varchar(),
	column8: varchar(),
	column9: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const adhesomeInteractionsInBronze = bronze.table("adhesome__interactions", {
	source: varchar("Source"),
	target: varchar("Target"),
	effect: varchar("Effect"),
	type: varchar("Type"),
	pmid: varchar("PMID"),
	dataSource: varchar("data_source"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const adhesomeAdhesomeAnnotationsInBronze = bronze.table("adhesome__adhesome_annotations", {
	officialSymbol: varchar("Official Symbol"),
	geneId: varchar("Gene ID"),
	proteinName: varchar("Protein name"),
	swissProtId: varchar("Swiss-Prot ID"),
	synonyms: varchar("Synonyms"),
	functionalCategory: varchar("Functional Category"),
	fa: varchar("FA"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const adhesomeComponentsInBronze = bronze.table("adhesome__components", {
	officialSymbol: varchar("Official Symbol"),
	geneId: varchar("Gene ID"),
	proteinName: varchar("Protein name"),
	swissProtId: varchar("Swiss-Prot ID"),
	synonyms: varchar("Synonyms"),
	functionalCategory: varchar("Functional Category"),
	fa: varchar("FA"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const adhesomeAdhesomeInteractionsInBronze = bronze.table("adhesome__adhesome_interactions", {
	source: varchar("Source"),
	target: varchar("Target"),
	effect: varchar("Effect"),
	type: varchar("Type"),
	pmid: varchar("PMID"),
	dataSource: varchar("data_source"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signorComplexesInBronze = bronze.table("signor__complexes", {
	signorId: varchar("SIGNOR ID"),
	complexName: varchar("COMPLEX NAME"),
	listOfEntities: varchar("LIST OF ENTITIES"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signorSignorComplexesInBronze = bronze.table("signor__signor_complexes", {
	signorId: varchar("SIGNOR ID"),
	complexName: varchar("COMPLEX NAME"),
	listOfEntities: varchar("LIST OF ENTITIES"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signorInteractionsInBronze = bronze.table("signor__interactions", {
	"#id(s)InteractorA": varchar("#ID(s) interactor A"),
	"id(s)InteractorB": varchar("ID(s) interactor B"),
	"altId(s)InteractorA": varchar("Alt. ID(s) interactor A"),
	"altId(s)InteractorB": varchar("Alt. ID(s) interactor B"),
	"alias(es)InteractorA": varchar("Alias(es) interactor A"),
	"alias(es)InteractorB": varchar("Alias(es) interactor B"),
	"interactionDetectionMethod(s)": varchar("Interaction detection method(s)"),
	"publication1StAuthor(s)": varchar("Publication 1st author(s)"),
	"publicationIdentifier(s)": varchar("Publication Identifier(s)"),
	taxidInteractorA: varchar("Taxid interactor A"),
	taxidInteractorB: varchar("Taxid interactor B"),
	"interactionType(s)": varchar("Interaction type(s)"),
	"sourceDatabase(s)": varchar("Source database(s)"),
	"interactionIdentifier(s)": varchar("Interaction identifier(s)"),
	"confidenceValue(s)": varchar("Confidence value(s)"),
	"expansionMethod(s)": varchar("Expansion method(s)"),
	"biologicalRole(s)InteractorA": varchar("Biological role(s) interactor A"),
	"biologicalRole(s)InteractorB": varchar("Biological role(s) interactor B"),
	"experimentalRole(s)InteractorA": varchar("Experimental role(s) interactor A"),
	"experimentalRole(s)InteractorB": varchar("Experimental role(s) interactor B"),
	"type(s)InteractorA": varchar("Type(s) interactor A"),
	"type(s)InteractorB": varchar("Type(s) interactor B"),
	"xref(s)InteractorA": varchar("Xref(s) interactor A"),
	"xref(s)InteractorB": varchar("Xref(s) interactor B"),
	"interactionXref(s)": varchar("Interaction Xref(s)"),
	"annotation(s)InteractorA": varchar("Annotation(s) interactor A"),
	"annotation(s)InteractorB": varchar("Annotation(s) interactor B"),
	"interactionAnnotation(s)": varchar("Interaction annotation(s)"),
	"hostOrganism(s)": varchar("Host organism(s)"),
	"interactionParameter(s)": varchar("Interaction parameter(s)"),
	creationDate: varchar("Creation date"),
	updateDate: varchar("UPDATE date"),
	"checksum(s)InteractorA": varchar("Checksum(s) interactor A"),
	"checksum(s)InteractorB": varchar("Checksum(s) interactor B"),
	"interactionChecksum(s)": varchar("Interaction Checksum(s)"),
	negative: varchar("Negative"),
	"feature(s)InteractorA": varchar("Feature(s) interactor A"),
	"feature(s)InteractorB": varchar("Feature(s) interactor B"),
	"stoichiometry(s)InteractorA": varchar("Stoichiometry(s) interactor A"),
	"stoichiometry(s)InteractorB": varchar("Stoichiometry(s) interactor B"),
	identificationMethodParticipantA: varchar("Identification METHOD participant A"),
	identificationMethodParticipantB: varchar("Identification METHOD participant B"),
	biologicalEffectInteractorA: varchar("Biological Effect interactor A"),
	biologicalEffectInteractorB: varchar("Biological Effect interactor B"),
	causalRegulatoryMechanism: varchar("Causal Regulatory Mechanism"),
	causalStatement: varchar("Causal statement"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signorProteinFamiliesInBronze = bronze.table("signor__protein_families", {
	signorId: varchar("SIGNOR ID"),
	protFamilyName: varchar("PROT. FAMILY NAME"),
	listOfEntities: varchar("LIST OF ENTITIES"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signorPhenotypesInBronze = bronze.table("signor__phenotypes", {
	signorId: varchar("SIGNOR ID"),
	phenotypeName: varchar("PHENOTYPE NAME"),
	phenotypeDescription: varchar("PHENOTYPE DESCRIPTION"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signorSignorStimuliInBronze = bronze.table("signor__signor_stimuli", {
	signorId: varchar("SIGNOR ID"),
	stimulusName: varchar("STIMULUS NAME"),
	stimulusDescription: varchar("STIMULUS DESCRIPTION"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signorSignorProteinFamiliesInBronze = bronze.table("signor__signor_protein_families", {
	signorId: varchar("SIGNOR ID"),
	protFamilyName: varchar("PROT. FAMILY NAME"),
	listOfEntities: varchar("LIST OF ENTITIES"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signorSignorPhenotypesInBronze = bronze.table("signor__signor_phenotypes", {
	signorId: varchar("SIGNOR ID"),
	phenotypeName: varchar("PHENOTYPE NAME"),
	phenotypeDescription: varchar("PHENOTYPE DESCRIPTION"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signorSignorInteractionsInBronze = bronze.table("signor__signor_interactions", {
	"#id(s)InteractorA": varchar("#ID(s) interactor A"),
	"id(s)InteractorB": varchar("ID(s) interactor B"),
	"altId(s)InteractorA": varchar("Alt. ID(s) interactor A"),
	"altId(s)InteractorB": varchar("Alt. ID(s) interactor B"),
	"alias(es)InteractorA": varchar("Alias(es) interactor A"),
	"alias(es)InteractorB": varchar("Alias(es) interactor B"),
	"interactionDetectionMethod(s)": varchar("Interaction detection method(s)"),
	"publication1StAuthor(s)": varchar("Publication 1st author(s)"),
	"publicationIdentifier(s)": varchar("Publication Identifier(s)"),
	taxidInteractorA: varchar("Taxid interactor A"),
	taxidInteractorB: varchar("Taxid interactor B"),
	"interactionType(s)": varchar("Interaction type(s)"),
	"sourceDatabase(s)": varchar("Source database(s)"),
	"interactionIdentifier(s)": varchar("Interaction identifier(s)"),
	"confidenceValue(s)": varchar("Confidence value(s)"),
	"expansionMethod(s)": varchar("Expansion method(s)"),
	"biologicalRole(s)InteractorA": varchar("Biological role(s) interactor A"),
	"biologicalRole(s)InteractorB": varchar("Biological role(s) interactor B"),
	"experimentalRole(s)InteractorA": varchar("Experimental role(s) interactor A"),
	"experimentalRole(s)InteractorB": varchar("Experimental role(s) interactor B"),
	"type(s)InteractorA": varchar("Type(s) interactor A"),
	"type(s)InteractorB": varchar("Type(s) interactor B"),
	"xref(s)InteractorA": varchar("Xref(s) interactor A"),
	"xref(s)InteractorB": varchar("Xref(s) interactor B"),
	"interactionXref(s)": varchar("Interaction Xref(s)"),
	"annotation(s)InteractorA": varchar("Annotation(s) interactor A"),
	"annotation(s)InteractorB": varchar("Annotation(s) interactor B"),
	"interactionAnnotation(s)": varchar("Interaction annotation(s)"),
	"hostOrganism(s)": varchar("Host organism(s)"),
	"interactionParameter(s)": varchar("Interaction parameter(s)"),
	creationDate: varchar("Creation date"),
	updateDate: varchar("UPDATE date"),
	"checksum(s)InteractorA": varchar("Checksum(s) interactor A"),
	"checksum(s)InteractorB": varchar("Checksum(s) interactor B"),
	"interactionChecksum(s)": varchar("Interaction Checksum(s)"),
	negative: varchar("Negative"),
	"feature(s)InteractorA": varchar("Feature(s) interactor A"),
	"feature(s)InteractorB": varchar("Feature(s) interactor B"),
	"stoichiometry(s)InteractorA": varchar("Stoichiometry(s) interactor A"),
	"stoichiometry(s)InteractorB": varchar("Stoichiometry(s) interactor B"),
	identificationMethodParticipantA: varchar("Identification METHOD participant A"),
	identificationMethodParticipantB: varchar("Identification METHOD participant B"),
	biologicalEffectInteractorA: varchar("Biological Effect interactor A"),
	biologicalEffectInteractorB: varchar("Biological Effect interactor B"),
	causalRegulatoryMechanism: varchar("Causal Regulatory Mechanism"),
	causalStatement: varchar("Causal statement"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const ca1InteractionsInBronze = bronze.table("ca1__interactions", {
	source: varchar("Source"),
	human: varchar("Human"),
	mouse: varchar("Mouse"),
	function: varchar("Function"),
	location: varchar("Location"),
	target: varchar("Target"),
	human1: varchar("Human_1"),
	mouse1: varchar("Mouse_1"),
	function1: varchar("Function_1"),
	location1: varchar("Location_1"),
	effects: varchar("Effects"),
	typeOfInteraction: varchar("Type of interaction"),
	pubMedId: varchar("PubMedID"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const ca1Ca1InteractionsInBronze = bronze.table("ca1__ca1_interactions", {
	source: varchar("Source"),
	human: varchar("Human"),
	mouse: varchar("Mouse"),
	function: varchar("Function"),
	location: varchar("Location"),
	target: varchar("Target"),
	human1: varchar("Human_1"),
	mouse1: varchar("Mouse_1"),
	function1: varchar("Function_1"),
	location1: varchar("Location_1"),
	effects: varchar("Effects"),
	typeOfInteraction: varchar("Type of interaction"),
	pubMedId: varchar("PubMedID"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const macrophageActivationPathwaysInBronze = bronze.table("macrophage__activation_pathways", {
	"unnamed:0": varchar("Unnamed: 0"),
	"unnamed:1": varchar("Unnamed: 1"),
	"unnamed:2": varchar("Unnamed: 2"),
	"unnamed:3": varchar("Unnamed: 3"),
	"unnamed:4": varchar("Unnamed: 4"),
	"unnamed:5": varchar("Unnamed: 5"),
	"unnamed:6": varchar("Unnamed: 6"),
	"unnamed:7": varchar("Unnamed: 7"),
	"unnamed:8": varchar("Unnamed: 8"),
	"unnamed:9": varchar("Unnamed: 9"),
	"unnamed:10": varchar("Unnamed: 10"),
	"unnamed:11": varchar("Unnamed: 11"),
	"unnamed:12": varchar("Unnamed: 12"),
	"unnamed:13": varchar("Unnamed: 13"),
	"unnamed:14": varchar("Unnamed: 14"),
	"unnamed:15": varchar("Unnamed: 15"),
	"unnamed:16": varchar("Unnamed: 16"),
	"unnamed:17": varchar("Unnamed: 17"),
	"unnamed:18": varchar("Unnamed: 18"),
	"unnamed:19": varchar("Unnamed: 19"),
	"unnamed:20": varchar("Unnamed: 20"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const macrophageMacrophageInteractionsInBronze = bronze.table("macrophage__macrophage_interactions", {
	"unnamed:0": varchar("Unnamed: 0"),
	"unnamed:1": varchar("Unnamed: 1"),
	"unnamed:2": varchar("Unnamed: 2"),
	"unnamed:3": varchar("Unnamed: 3"),
	"unnamed:4": varchar("Unnamed: 4"),
	"unnamed:5": varchar("Unnamed: 5"),
	"unnamed:6": varchar("Unnamed: 6"),
	"unnamed:7": varchar("Unnamed: 7"),
	"unnamed:8": varchar("Unnamed: 8"),
	"unnamed:9": varchar("Unnamed: 9"),
	"unnamed:10": varchar("Unnamed: 10"),
	"unnamed:11": varchar("Unnamed: 11"),
	"unnamed:12": varchar("Unnamed: 12"),
	"unnamed:13": varchar("Unnamed: 13"),
	"unnamed:14": varchar("Unnamed: 14"),
	"unnamed:15": varchar("Unnamed: 15"),
	"unnamed:16": varchar("Unnamed: 16"),
	"unnamed:17": varchar("Unnamed: 17"),
	"unnamed:18": varchar("Unnamed: 18"),
	"unnamed:19": varchar("Unnamed: 19"),
	"unnamed:20": varchar("Unnamed: 20"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const opmProteinsAnnotationsInBronze = bronze.table("opm_proteins__annotations", {
	id: varchar(),
	ordering: varchar(),
	familyNameCache: varchar("family_name_cache"),
	speciesNameCache: varchar("species_name_cache"),
	membraneNameCache: varchar("membrane_name_cache"),
	name: varchar(),
	description: varchar(),
	comments: varchar(),
	pdbid: varchar(),
	resolution: varchar(),
	topologySubunit: varchar("topology_subunit"),
	topologyShowIn: varchar("topology_show_in"),
	thickness: varchar(),
	thicknesserror: varchar(),
	subunitSegments: varchar("subunit_segments"),
	tilt: varchar(),
	tilterror: varchar(),
	gibbs: varchar(),
	tau: varchar(),
	verification: varchar(),
	membraneId: varchar("membrane_id"),
	speciesId: varchar("species_id"),
	familyId: varchar("family_id"),
	superfamilyId: varchar("superfamily_id"),
	classtypeId: varchar("classtype_id"),
	typeId: varchar("type_id"),
	secondaryRepresentationsCount: varchar("secondary_representations_count"),
	structureSubunitsCount: varchar("structure_subunits_count"),
	citationsCount: varchar("citations_count"),
	createdAt: varchar("created_at"),
	updatedAt: varchar("updated_at"),
	uniprotcode: varchar(),
	interpro: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const opmProteinsOpmMembraneProteinsInBronze = bronze.table("opm_proteins__opm_membrane_proteins", {
	id: varchar(),
	ordering: varchar(),
	familyNameCache: varchar("family_name_cache"),
	speciesNameCache: varchar("species_name_cache"),
	membraneNameCache: varchar("membrane_name_cache"),
	name: varchar(),
	description: varchar(),
	comments: varchar(),
	pdbid: varchar(),
	resolution: varchar(),
	topologySubunit: varchar("topology_subunit"),
	topologyShowIn: varchar("topology_show_in"),
	thickness: varchar(),
	thicknesserror: varchar(),
	subunitSegments: varchar("subunit_segments"),
	tilt: varchar(),
	tilterror: varchar(),
	gibbs: varchar(),
	tau: varchar(),
	verification: varchar(),
	membraneId: varchar("membrane_id"),
	speciesId: varchar("species_id"),
	familyId: varchar("family_id"),
	superfamilyId: varchar("superfamily_id"),
	classtypeId: varchar("classtype_id"),
	typeId: varchar("type_id"),
	secondaryRepresentationsCount: varchar("secondary_representations_count"),
	structureSubunitsCount: varchar("structure_subunits_count"),
	citationsCount: varchar("citations_count"),
	createdAt: varchar("created_at"),
	updatedAt: varchar("updated_at"),
	uniprotcode: varchar(),
	interpro: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const talklrInteractionsInBronze = bronze.table("talklr__interactions", {
	pairName: varchar("Pair.Name"),
	ligandApprovedSymbol: varchar("Ligand.ApprovedSymbol"),
	ligandName: varchar("Ligand.Name"),
	receptorApprovedSymbol: varchar("Receptor.ApprovedSymbol"),
	receptorName: varchar("Receptor.Name"),
	dlrp: varchar("DLRP"),
	hpmr: varchar("HPMR"),
	iuphar: varchar("IUPHAR"),
	hprd: varchar("HPRD"),
	stringBinding: varchar("STRING.binding"),
	stringExperiment: varchar("STRING.experiment"),
	hpmrLigand: varchar("HPMR.Ligand"),
	hpmrReceptor: varchar("HPMR.Receptor"),
	pmidManual: varchar("PMID.Manual"),
	pairSource: varchar("Pair.Source"),
	pairEvidence: varchar("Pair.Evidence"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const talklrTalklrAnnotationsInBronze = bronze.table("talklr__talklr_annotations", {
	pairName: varchar("Pair.Name"),
	ligandApprovedSymbol: varchar("Ligand.ApprovedSymbol"),
	ligandName: varchar("Ligand.Name"),
	receptorApprovedSymbol: varchar("Receptor.ApprovedSymbol"),
	receptorName: varchar("Receptor.Name"),
	dlrp: varchar("DLRP"),
	hpmr: varchar("HPMR"),
	iuphar: varchar("IUPHAR"),
	hprd: varchar("HPRD"),
	stringBinding: varchar("STRING.binding"),
	stringExperiment: varchar("STRING.experiment"),
	hpmrLigand: varchar("HPMR.Ligand"),
	hpmrReceptor: varchar("HPMR.Receptor"),
	pmidManual: varchar("PMID.Manual"),
	pairSource: varchar("Pair.Source"),
	pairEvidence: varchar("Pair.Evidence"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const talklrTalklrInteractionsInBronze = bronze.table("talklr__talklr_interactions", {
	pairName: varchar("Pair.Name"),
	ligandApprovedSymbol: varchar("Ligand.ApprovedSymbol"),
	ligandName: varchar("Ligand.Name"),
	receptorApprovedSymbol: varchar("Receptor.ApprovedSymbol"),
	receptorName: varchar("Receptor.Name"),
	dlrp: varchar("DLRP"),
	hpmr: varchar("HPMR"),
	iuphar: varchar("IUPHAR"),
	hprd: varchar("HPRD"),
	stringBinding: varchar("STRING.binding"),
	stringExperiment: varchar("STRING.experiment"),
	hpmrLigand: varchar("HPMR.Ligand"),
	hpmrReceptor: varchar("HPMR.Receptor"),
	pmidManual: varchar("PMID.Manual"),
	pairSource: varchar("Pair.Source"),
	pairEvidence: varchar("Pair.Evidence"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const dominoInteractionsInBronze = bronze.table("domino__interactions", {
	idInteractorA: varchar("ID interactor A"),
	idInteractorB: varchar("ID interactor B"),
	altIdInteractorA: varchar("Alt. ID interactor A"),
	altIdInteractorB: varchar("Alt. ID interactor B"),
	"alias(es)InteractorA": varchar("Alias(es) interactor A"),
	"alias(es)InteractorB": varchar("Alias(es) interactor B"),
	"interactionDetectionMethod(s)": varchar("Interaction detection method(s)"),
	"publication1StAuthor(s)": varchar("Publication 1st author(s)"),
	"publicationIdentifier(s)": varchar("Publication Identifier(s)"),
	taxidInteractorA: varchar("Taxid interactor A"),
	taxidInteractorB: varchar("Taxid interactor B"),
	"interactionType(s)": varchar("Interaction type(s)"),
	"sourceDatabase(s)": varchar("Source database(s)"),
	"interactionIdentifier(s)": varchar("Interaction identifier(s)"),
	"confidenceValue(s)": varchar("Confidence value(s)"),
	expansion: varchar(),
	biologicalRoleA: varchar("biological role A"),
	biologicalRoleB: varchar("biological role B"),
	experimentalRoleA: varchar("experimental role A"),
	experimentalRoleB: varchar("experimental role B"),
	interactorTypeB: varchar("interactor type B"),
	interactorTypeB1: varchar("interactor type B_1"),
	xrefsA: varchar("xrefs A"),
	xrefsB: varchar("xrefs B"),
	xrefsInteraction: varchar("xrefs Interaction"),
	annotationsA: varchar("Annotations A"),
	annotationsB: varchar("Annotations B"),
	interactionAnnotations: varchar("Interaction Annotations"),
	hostOrganismTaxid: varchar("Host organism taxid"),
	parametersInteraction: varchar("parameters Interaction"),
	dataset: varchar(),
	cautionInteraction: varchar("Caution Interaction"),
	bindingSiteA: varchar("binding site A"),
	bindingSiteB: varchar("binding site B"),
	ptmA: varchar("ptm A"),
	ptmB: varchar("ptm B"),
	mutationsA: varchar("mutations A"),
	mutationsB: varchar("mutations B"),
	negative: varchar(),
	inference: varchar(),
	column40: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const dominoDominoInteractionsInBronze = bronze.table("domino__domino_interactions", {
	idInteractorA: varchar("ID interactor A"),
	idInteractorB: varchar("ID interactor B"),
	altIdInteractorA: varchar("Alt. ID interactor A"),
	altIdInteractorB: varchar("Alt. ID interactor B"),
	"alias(es)InteractorA": varchar("Alias(es) interactor A"),
	"alias(es)InteractorB": varchar("Alias(es) interactor B"),
	"interactionDetectionMethod(s)": varchar("Interaction detection method(s)"),
	"publication1StAuthor(s)": varchar("Publication 1st author(s)"),
	"publicationIdentifier(s)": varchar("Publication Identifier(s)"),
	taxidInteractorA: varchar("Taxid interactor A"),
	taxidInteractorB: varchar("Taxid interactor B"),
	"interactionType(s)": varchar("Interaction type(s)"),
	"sourceDatabase(s)": varchar("Source database(s)"),
	"interactionIdentifier(s)": varchar("Interaction identifier(s)"),
	"confidenceValue(s)": varchar("Confidence value(s)"),
	expansion: varchar(),
	biologicalRoleA: varchar("biological role A"),
	biologicalRoleB: varchar("biological role B"),
	experimentalRoleA: varchar("experimental role A"),
	experimentalRoleB: varchar("experimental role B"),
	interactorTypeB: varchar("interactor type B"),
	interactorTypeB1: varchar("interactor type B_1"),
	xrefsA: varchar("xrefs A"),
	xrefsB: varchar("xrefs B"),
	xrefsInteraction: varchar("xrefs Interaction"),
	annotationsA: varchar("Annotations A"),
	annotationsB: varchar("Annotations B"),
	interactionAnnotations: varchar("Interaction Annotations"),
	hostOrganismTaxid: varchar("Host organism taxid"),
	parametersInteraction: varchar("parameters Interaction"),
	dataset: varchar(),
	cautionInteraction: varchar("Caution Interaction"),
	bindingSiteA: varchar("binding site A"),
	bindingSiteB: varchar("binding site B"),
	ptmA: varchar("ptm A"),
	ptmB: varchar("ptm B"),
	mutationsA: varchar("mutations A"),
	mutationsB: varchar("mutations B"),
	negative: varchar(),
	inference: varchar(),
	column40: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const biogridInteractionsInBronze = bronze.table("biogrid__interactions", {
	idInteractorA: varchar("ID Interactor A"),
	idInteractorB: varchar("ID Interactor B"),
	altIDsInteractorA: varchar("Alt IDs Interactor A"),
	altIDsInteractorB: varchar("Alt IDs Interactor B"),
	aliasesInteractorA: varchar("Aliases Interactor A"),
	aliasesInteractorB: varchar("Aliases Interactor B"),
	interactionDetectionMethod: varchar("Interaction Detection Method"),
	publication1StAuthor: varchar("Publication 1st Author"),
	publicationIdentifiers: varchar("Publication Identifiers"),
	taxidInteractorA: varchar("Taxid Interactor A"),
	taxidInteractorB: varchar("Taxid Interactor B"),
	interactionTypes: varchar("Interaction Types"),
	sourceDatabase: varchar("Source Database"),
	interactionIdentifiers: varchar("Interaction Identifiers"),
	confidenceValues: varchar("Confidence Values"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const biogridBiogridInteractionsInBronze = bronze.table("biogrid__biogrid_interactions", {
	idInteractorA: varchar("ID Interactor A"),
	idInteractorB: varchar("ID Interactor B"),
	altIDsInteractorA: varchar("Alt IDs Interactor A"),
	altIDsInteractorB: varchar("Alt IDs Interactor B"),
	aliasesInteractorA: varchar("Aliases Interactor A"),
	aliasesInteractorB: varchar("Aliases Interactor B"),
	interactionDetectionMethod: varchar("Interaction Detection Method"),
	publication1StAuthor: varchar("Publication 1st Author"),
	publicationIdentifiers: varchar("Publication Identifiers"),
	taxidInteractorA: varchar("Taxid Interactor A"),
	taxidInteractorB: varchar("Taxid Interactor B"),
	interactionTypes: varchar("Interaction Types"),
	sourceDatabase: varchar("Source Database"),
	interactionIdentifiers: varchar("Interaction Identifiers"),
	confidenceValues: varchar("Confidence Values"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const guide2PharmaInteractionsInBronze = bronze.table("guide2pharma__interactions", {
	"gtoPdbVersion:20252Published:20250618": varchar("GtoPdb Version: 2025.2 - published: 2025-06-18"),
	column01: varchar(),
	column02: varchar(),
	column03: varchar(),
	column04: varchar(),
	column05: varchar(),
	column06: varchar(),
	column07: varchar(),
	column08: varchar(),
	column09: varchar(),
	column10: varchar(),
	column11: varchar(),
	column12: varchar(),
	column13: varchar(),
	column14: varchar(),
	column15: varchar(),
	column16: varchar(),
	column17: varchar(),
	column18: varchar(),
	column19: varchar(),
	column20: varchar(),
	column21: varchar(),
	column22: varchar(),
	column23: varchar(),
	column24: varchar(),
	column25: varchar(),
	column26: varchar(),
	column27: varchar(),
	column28: varchar(),
	column29: varchar(),
	column30: varchar(),
	column31: varchar(),
	column32: varchar(),
	column33: varchar(),
	column34: varchar(),
	column35: varchar(),
	column36: varchar(),
	column37: varchar(),
	column38: varchar(),
	column39: varchar(),
	column40: varchar(),
	column41: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const guide2PharmaGuide2PharmaInteractionsInBronze = bronze.table("guide2pharma__guide2pharma_interactions", {
	"gtoPdbVersion:20252Published:20250618": varchar("GtoPdb Version: 2025.2 - published: 2025-06-18"),
	column01: varchar(),
	column02: varchar(),
	column03: varchar(),
	column04: varchar(),
	column05: varchar(),
	column06: varchar(),
	column07: varchar(),
	column08: varchar(),
	column09: varchar(),
	column10: varchar(),
	column11: varchar(),
	column12: varchar(),
	column13: varchar(),
	column14: varchar(),
	column15: varchar(),
	column16: varchar(),
	column17: varchar(),
	column18: varchar(),
	column19: varchar(),
	column20: varchar(),
	column21: varchar(),
	column22: varchar(),
	column23: varchar(),
	column24: varchar(),
	column25: varchar(),
	column26: varchar(),
	column27: varchar(),
	column28: varchar(),
	column29: varchar(),
	column30: varchar(),
	column31: varchar(),
	column32: varchar(),
	column33: varchar(),
	column34: varchar(),
	column35: varchar(),
	column36: varchar(),
	column37: varchar(),
	column38: varchar(),
	column39: varchar(),
	column40: varchar(),
	column41: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const connectomedb2020InteractionsInBronze = bronze.table("connectomedb2020__interactions", {
	ligandGeneSymbol: varchar("Ligand gene symbol"),
	ligandHgncId: varchar("Ligand HGNC ID"),
	ligandLocation: varchar("Ligand location"),
	receptorGeneSymbol: varchar("Receptor gene symbol"),
	receptorHgncId: varchar("Receptor HGNC ID"),
	pmidSupport: varchar("PMID support"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const connectomedb2020Connectomedb2020InteractionsInBronze = bronze.table("connectomedb2020__connectomedb2020_interactions", {
	ligandGeneSymbol: varchar("Ligand gene symbol"),
	ligandHgncId: varchar("Ligand HGNC ID"),
	ligandLocation: varchar("Ligand location"),
	receptorGeneSymbol: varchar("Receptor gene symbol"),
	receptorHgncId: varchar("Receptor HGNC ID"),
	pmidSupport: varchar("PMID support"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const hprdHprdPtmsInBronze = bronze.table("hprd__hprd_ptms", {
	column00: varchar(),
	column01: varchar(),
	column02: varchar(),
	column03: varchar(),
	column04: varchar(),
	column05: varchar(),
	column06: varchar(),
	column07: varchar(),
	column08: varchar(),
	column09: varchar(),
	column10: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const hprdInteractionsInBronze = bronze.table("hprd__interactions", {
	column00: varchar(),
	column01: varchar(),
	column02: varchar(),
	column03: varchar(),
	column04: varchar(),
	column05: varchar(),
	column06: varchar(),
	column07: varchar(),
	column08: varchar(),
	column09: varchar(),
	column10: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const hprdHprdInteractionsInBronze = bronze.table("hprd__hprd_interactions", {
	column0: varchar(),
	column1: varchar(),
	column2: varchar(),
	column3: varchar(),
	column4: varchar(),
	column5: varchar(),
	column6: varchar(),
	column7: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const deathdomainDeathdomainInteractionsInBronze = bronze.table("deathdomain__deathdomain_interactions", {
	proteinA: varchar("ProteinA"),
	proteinB: varchar("ProteinB"),
	methods: varchar("Methods"),
	references: varchar("References"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const deathdomainInteractionsInBronze = bronze.table("deathdomain__interactions", {
	proteinA: varchar("ProteinA"),
	proteinB: varchar("ProteinB"),
	methods: varchar("Methods"),
	references: varchar("References"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const celltalkdbInteractionsInBronze = bronze.table("celltalkdb__interactions", {
	lrPair: varchar("lr_pair"),
	ligandGeneSymbol: varchar("ligand_gene_symbol"),
	receptorGeneSymbol: varchar("receptor_gene_symbol"),
	ligandGeneId: varchar("ligand_gene_id"),
	receptorGeneId: varchar("receptor_gene_id"),
	ligandEnsemblProteinId: varchar("ligand_ensembl_protein_id"),
	receptorEnsemblProteinId: varchar("receptor_ensembl_protein_id"),
	ligandEnsemblGeneId: varchar("ligand_ensembl_gene_id"),
	receptorEnsemblGeneId: varchar("receptor_ensembl_gene_id"),
	evidence: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const celltalkdbCelltalkdbInteractionsInBronze = bronze.table("celltalkdb__celltalkdb_interactions", {
	lrPair: varchar("lr_pair"),
	ligandGeneSymbol: varchar("ligand_gene_symbol"),
	receptorGeneSymbol: varchar("receptor_gene_symbol"),
	ligandGeneId: varchar("ligand_gene_id"),
	receptorGeneId: varchar("receptor_gene_id"),
	ligandEnsemblProteinId: varchar("ligand_ensembl_protein_id"),
	receptorEnsemblProteinId: varchar("receptor_ensembl_protein_id"),
	ligandEnsemblGeneId: varchar("ligand_ensembl_gene_id"),
	receptorEnsemblGeneId: varchar("receptor_ensembl_gene_id"),
	evidence: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const lmpidInteractionsInBronze = bronze.table("lmpid__interactions", {
	baitUniprotId: varchar("bait_uniprot_id"),
	preyUniprotId: varchar("prey_uniprot_id"),
	references: varchar(),
	sequencePosition: varchar("sequence_position"),
	motifInstance: varchar("motif_instance"),
	interactingDomain: varchar("interacting_domain"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const lmpidLmpidDmiInBronze = bronze.table("lmpid__lmpid_dmi", {
	baitUniprotId: varchar("bait_uniprot_id"),
	preyUniprotId: varchar("prey_uniprot_id"),
	references: varchar(),
	sequencePosition: varchar("sequence_position"),
	motifInstance: varchar("motif_instance"),
	interactingDomain: varchar("interacting_domain"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const lmpidLmpidInteractionsInBronze = bronze.table("lmpid__lmpid_interactions", {
	baitUniprotId: varchar("bait_uniprot_id"),
	preyUniprotId: varchar("prey_uniprot_id"),
	references: varchar(),
	sequencePosition: varchar("sequence_position"),
	motifInstance: varchar("motif_instance"),
	interactingDomain: varchar("interacting_domain"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const adhesomeComponentsAnnotationsInBronze = bronze.table("adhesome_components__annotations", {
	officialSymbol: varchar("Official Symbol"),
	geneId: varchar("Gene ID"),
	proteinName: varchar("Protein name"),
	swissProtId: varchar("Swiss-Prot ID"),
	synonyms: varchar("Synonyms"),
	functionalCategory: varchar("Functional Category"),
	fa: varchar("FA"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const scconnectInteractionsInBronze = bronze.table("scconnect__interactions", {
	"interactionId,target,targetId,targetGeneSymbol,targetUnipr": varchar("interaction_id,target,target_id,target_gene_symbol,target_unipr"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const scconnectScconnectInteractionsInBronze = bronze.table("scconnect__scconnect_interactions", {
	"interactionId,target,targetId,targetGeneSymbol,targetUnipr": varchar("interaction_id,target,target_id,target_gene_symbol,target_unipr"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const arnInteractionsInBronze = bronze.table("arn__interactions", {
	column0: varchar(),
	column1: varchar(),
	column2: varchar(),
	column3: varchar(),
	column4: varchar(),
	column5: varchar(),
	column6: varchar(),
	column7: varchar(),
	column8: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const arnArnInteractionsInBronze = bronze.table("arn__arn_interactions", {
	column0: varchar(),
	column1: varchar(),
	column2: varchar(),
	column3: varchar(),
	column4: varchar(),
	column5: varchar(),
	column6: varchar(),
	column7: varchar(),
	column8: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signalink3Signalink3InteractionsInBronze = bronze.table("signalink3__signalink3_interactions", {
	interactorANodeName: varchar("interactor_a_node_name"),
	interactorBNodeName: varchar("interactor_b_node_name"),
	interactionDetectionMethod: varchar("interaction_detection_method"),
	firstAuthor: varchar("first_author"),
	publicationIds: varchar("publication_ids"),
	interactionTypes: varchar("interaction_types"),
	sourceDb: varchar("source_db"),
	interactionIdentifiers: varchar("interaction_identifiers"),
	confidenceScores: varchar("confidence_scores"),
	layer: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signalink3NodesInBronze = bronze.table("signalink3__nodes", {
	id: varchar(),
	name: varchar(),
	altAccession: varchar("alt_accession"),
	taxId: varchar("tax_id"),
	pathways: varchar(),
	aliases: varchar(),
	topology: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signalink3Signalink3AnnotationsInBronze = bronze.table("signalink3__signalink3_annotations", {
	id: varchar(),
	name: varchar(),
	altAccession: varchar("alt_accession"),
	taxId: varchar("tax_id"),
	pathways: varchar(),
	aliases: varchar(),
	topology: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const signalink3EdgesInBronze = bronze.table("signalink3__edges", {
	interactorANodeName: varchar("interactor_a_node_name"),
	interactorBNodeName: varchar("interactor_b_node_name"),
	interactionDetectionMethod: varchar("interaction_detection_method"),
	firstAuthor: varchar("first_author"),
	publicationIds: varchar("publication_ids"),
	interactionTypes: varchar("interaction_types"),
	sourceDb: varchar("source_db"),
	interactionIdentifiers: varchar("interaction_identifiers"),
	confidenceScores: varchar("confidence_scores"),
	layer: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const celllinkerInteractionsInBronze = bronze.table("celllinker__interactions", {
	ligandId: varchar("Ligand_id"),
	ligandSymbol: varchar("Ligand_symbol"),
	ligandLocation: varchar("Ligand_location"),
	receptorId: varchar("Receptor_id"),
	receptorSymbol: varchar("Receptor_symbol"),
	receptorLocation: varchar("Receptor.location"),
	type: varchar("Type"),
	keggPathway: varchar("KEGG.pathway"),
	pmubmedId: varchar("Pmubmed.ID"),
	otherDb: varchar("Other.DB"),
	lrid: varchar("LRID"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const celllinkerCelllinkerInteractionsInBronze = bronze.table("celllinker__celllinker_interactions", {
	ligandId: varchar("Ligand_id"),
	ligandSymbol: varchar("Ligand_symbol"),
	ligandLocation: varchar("Ligand_location"),
	receptorId: varchar("Receptor_id"),
	receptorSymbol: varchar("Receptor_symbol"),
	receptorLocation: varchar("Receptor.location"),
	type: varchar("Type"),
	keggPathway: varchar("KEGG.pathway"),
	pmubmedId: varchar("Pmubmed.ID"),
	otherDb: varchar("Other.DB"),
	lrid: varchar("LRID"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const celllinkerCelllinkerSmallMoleculeInteractionsInBronze = bronze.table("celllinker__celllinker_small_molecule_interactions", {
	ligandPubchemSid: varchar("ligand_pubchem_sid"),
	ligandPubchemCid: varchar("ligand_pubchem_cid"),
	ligandName: varchar("ligand name"),
	ligandType: varchar("ligand_type"),
	receptorId: varchar("Receptor_id"),
	receptorSymbol: varchar("Receptor_symbol"),
	receptorUniprot: varchar("Receptor_uniprot"),
	receptorLocation: varchar("Receptor_location"),
	type: varchar("Type"),
	targetSpecies: varchar("target_species"),
	pubmedId: varchar("pubmed_id"),
	otherDb: varchar("Other.DB"),
	lrid: varchar("LRID"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const celllinkerCelllinkerComplexesInBronze = bronze.table("celllinker__celllinker_complexes", {
	ligandCompondId: varchar("ligand compond ID"),
	receptorCompondId: varchar("receptor compond ID"),
	subunitCount: varchar("subunit count"),
	subunit1: varchar("subunit 1"),
	subunit11: varchar("subunit 1_1"),
	subunit2: varchar("subunit 2"),
	subunit21: varchar("subunit 2_1"),
	subunit3: varchar("subunit 3"),
	subunit31: varchar("subunit 3_1"),
	subunit4: varchar("subunit 4"),
	subunit41: varchar("subunit 4_1"),
	subunit5: varchar("subunit 5"),
	subunit51: varchar("subunit 5_1"),
	compondLocate: varchar("compond locate"),
	compond: varchar("Compond"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const phosphoelmInteractionsInBronze = bronze.table("phosphoelm__interactions", {
	acc: varchar(),
	sequence: varchar(),
	position: varchar(),
	code: varchar(),
	pmids: varchar(),
	kinases: varchar(),
	source: varchar(),
	species: varchar(),
	entryDate: varchar("entry_date"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const phosphoelmPhosphoelmInteractionsInBronze = bronze.table("phosphoelm__phosphoelm_interactions", {
	acc: varchar(),
	sequence: varchar(),
	position: varchar(),
	code: varchar(),
	pmids: varchar(),
	kinases: varchar(),
	source: varchar(),
	species: varchar(),
	entryDate: varchar("entry_date"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const phosphositeInteractionsInBronze = bronze.table("phosphosite__interactions", {
	"050522": varchar(),
	column01: varchar(),
	column02: varchar(),
	column03: varchar(),
	column04: varchar(),
	column05: varchar(),
	column06: varchar(),
	column07: varchar(),
	column08: varchar(),
	column09: varchar(),
	column10: varchar(),
	column11: varchar(),
	column12: varchar(),
	column13: varchar(),
	column14: varchar(),
	column15: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const phosphositeKinaseSubstrateInteractionsInBronze = bronze.table("phosphosite__kinase_substrate_interactions", {
	"050522": varchar(),
	column01: varchar(),
	column02: varchar(),
	column03: varchar(),
	column04: varchar(),
	column05: varchar(),
	column06: varchar(),
	column07: varchar(),
	column08: varchar(),
	column09: varchar(),
	column10: varchar(),
	column11: varchar(),
	column12: varchar(),
	column13: varchar(),
	column14: varchar(),
	column15: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const panglaodbAnnotationsInBronze = bronze.table("panglaodb__annotations", {
	species: varchar(),
	officialGeneSymbol: varchar("official gene symbol"),
	cellType: varchar("cell type"),
	nicknames: varchar(),
	ubiquitousnessIndex: varchar("ubiquitousness index"),
	productDescription: varchar("product description"),
	geneType: varchar("gene type"),
	canonicalMarker: varchar("canonical marker"),
	germLayer: varchar("germ layer"),
	organ: varchar(),
	sensitivityHuman: varchar("sensitivity_human"),
	sensitivityMouse: varchar("sensitivity_mouse"),
	specificityHuman: varchar("specificity_human"),
	specificityMouse: varchar("specificity_mouse"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const depodInteractionsInBronze = bronze.table("depod__interactions", {
	phosphatase: varchar("Phosphatase"),
	substrate: varchar("Substrate"),
	substrateType: varchar("Substrate type"),
	substrateSourceOrganism: varchar("Substrate source organism"),
	dephosphorylationSite: varchar("Dephosphorylation site"),
	bioassayType: varchar("Bioassay type"),
	pubMedId: varchar("PubMed ID"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const depodDepodEnzymeSubstrateInBronze = bronze.table("depod__depod_enzyme_substrate", {
	phosphatase: varchar("Phosphatase"),
	substrate: varchar("Substrate"),
	substrateType: varchar("Substrate type"),
	substrateSourceOrganism: varchar("Substrate source organism"),
	dephosphorylationSite: varchar("Dephosphorylation site"),
	bioassayType: varchar("Bioassay type"),
	pubMedId: varchar("PubMed ID"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const psiMiCvTermsInBronze = bronze.table("psi_mi__cv_terms", {
	stanzaType: varchar("_stanza_type"),
	altIds: varchar("alt_ids"),
	comment: varchar(),
	createdBy: varchar("created_by"),
	creationDate: varchar("creation_date"),
	definition: varchar(),
	definitionReferences: varchar("definition_references"),
	id: varchar(),
	isObsolete: varchar("is_obsolete"),
	name: varchar(),
	parentIds: varchar("parent_ids"),
	propertyValue: varchar("property_value"),
	subset: varchar(),
	synonymTexts: varchar("synonym_texts"),
	xrefs: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});
export const tripInteractionsInBronze = bronze.table("trip__interactions", {
	source: varchar(),
	target: varchar(),
	references: varchar(),
	methods: varchar(),
	effect: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const tripTripInteractionsInBronze = bronze.table("trip__trip_interactions", {
	source: varchar(),
	target: varchar(),
	references: varchar(),
	methods: varchar(),
	effect: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const icellnetInteractionsInBronze = bronze.table("icellnet__interactions", {
	ligand1: varchar("Ligand 1"),
	ligand2: varchar("Ligand 2"),
	ligand3: varchar("Ligand 3"),
	ligand4: varchar("Ligand 4"),
	receptor1: varchar("Receptor 1"),
	receptor2: varchar("Receptor 2"),
	receptor3: varchar("Receptor 3"),
	receptor4: varchar("Receptor 4"),
	receptor5: varchar("Receptor 5"),
	alias: varchar("Alias"),
	family: varchar("Family"),
	subfamily: varchar("Subfamily"),
	otherFamily: varchar("Other family"),
	reference: varchar("Reference"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const icellnetIcellnetInteractionsInBronze = bronze.table("icellnet__icellnet_interactions", {
	ligand1: varchar("Ligand 1"),
	ligand2: varchar("Ligand 2"),
	ligand3: varchar("Ligand 3"),
	ligand4: varchar("Ligand 4"),
	receptor1: varchar("Receptor 1"),
	receptor2: varchar("Receptor 2"),
	receptor3: varchar("Receptor 3"),
	receptor4: varchar("Receptor 4"),
	receptor5: varchar("Receptor 5"),
	alias: varchar("Alias"),
	family: varchar("Family"),
	subfamily: varchar("Subfamily"),
	otherFamily: varchar("Other family"),
	reference: varchar("Reference"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const pdzbaseInteractionsInBronze = bronze.table("pdzbase__interactions", {
	source: varchar(),
	target: varchar(),
	sourceIsoform: varchar("source_isoform"),
	targetIsoform: varchar("target_isoform"),
	sourceGenesymbol: varchar("source_genesymbol"),
	targetGenesymbol: varchar("target_genesymbol"),
	pdzDomain: varchar("pdz_domain"),
	organism: varchar(),
	references: varchar(),
	sourceDatabase: varchar("source_database"),
	processedDate: varchar("processed_date"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const pdzbasePdzbaseInteractionsInBronze = bronze.table("pdzbase__pdzbase_interactions", {
	source: varchar(),
	target: varchar(),
	sourceIsoform: varchar("source_isoform"),
	targetIsoform: varchar("target_isoform"),
	sourceGenesymbol: varchar("source_genesymbol"),
	targetGenesymbol: varchar("target_genesymbol"),
	pdzDomain: varchar("pdz_domain"),
	organism: varchar(),
	references: varchar(),
	sourceDatabase: varchar("source_database"),
	processedDate: varchar("processed_date"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const omnipathCvTermsInBronze = bronze.table("omnipath__cv_terms", {
	stanzaType: varchar("_stanza_type"),
	definition: varchar(),
	id: varchar(),
	name: varchar(),
	namespace: varchar(),
	synonymTexts: varchar("synonym_texts"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const nrf2OmeInteractionsInBronze = bronze.table("nrf2ome__interactions", {
	column0: varchar(),
	column1: varchar(),
	column2: varchar(),
	column3: varchar(),
	column4: varchar(),
	column5: varchar(),
	column6: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const nrf2OmeNrf2OmeInteractionsInBronze = bronze.table("nrf2ome__nrf2ome_interactions", {
	column0: varchar(),
	column1: varchar(),
	column2: varchar(),
	column3: varchar(),
	column4: varchar(),
	column5: varchar(),
	column6: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const uniprotUniprotEntitiesInBronze = bronze.table("uniprot__uniprot_entities", {
	entry: varchar("Entry"),
	entryName: varchar("Entry Name"),
	proteinNames: varchar("Protein names"),
	length: varchar("Length"),
	geneOntologyIDs: varchar("Gene Ontology IDs"),
	keywordId: varchar("Keyword ID"),
	mass: varchar("Mass"),
	sequence: varchar("Sequence"),
	"geneNames (primary)": varchar("Gene Names (primary)"),
	"geneNames (synonym)": varchar("Gene Names (synonym)"),
	bioGrid: varchar("BioGRID"),
	string: varchar("STRING"),
	"organism (id)": varchar("Organism (ID)"),
	"alternativeProducts (isoforms)": varchar("Alternative products (isoforms)"),
	involvementInDisease: varchar("Involvement in disease"),
	mutagenesis: varchar("Mutagenesis"),
	"subcellularLocation [cc]": varchar("Subcellular location [CC]"),
	postTranslationalModification: varchar("Post-translational modification"),
	pubMedId: varchar("PubMed ID"),
	"function [cc]": varchar("Function [CC]"),
	ensembl: varchar("Ensembl"),
	kegg: varchar("KEGG"),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const uniprotKeywordsInBronze = bronze.table("uniprot__keywords", {
	stanzaType: varchar("_stanza_type"),
	categoryAccession: varchar("category_accession"),
	definition: varchar(),
	id: varchar(),
	name: varchar(),
	parentIds: varchar("parent_ids"),
	relationship: varchar(),
	synonymTexts: varchar("synonym_texts"),
	xrefs: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const uniprotUniprotKeywordsInBronze = bronze.table("uniprot__uniprot_keywords", {
	stanzaType: varchar("_stanza_type"),
	categoryAccession: varchar("category_accession"),
	definition: varchar(),
	id: varchar(),
	name: varchar(),
	parentIds: varchar("parent_ids"),
	relationship: varchar(),
	synonymTexts: varchar("synonym_texts"),
	xrefs: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const uniprotEntitiesInBronze = bronze.table("uniprot__entities", {
	entry: varchar("Entry"),
	entryName: varchar("Entry Name"),
	proteinNames: varchar("Protein names"),
	length: varchar("Length"),
	geneOntologyIDs: varchar("Gene Ontology IDs"),
	keywordId: varchar("Keyword ID"),
	mass: varchar("Mass"),
	sequence: varchar("Sequence"),
	"geneNames (primary)": varchar("Gene Names (primary)"),
	"geneNames (synonym)": varchar("Gene Names (synonym)"),
	bioGrid: varchar("BioGRID"),
	string: varchar("STRING"),
	"organism (id)": varchar("Organism (ID)"),
	"alternativeProducts (isoforms)": varchar("Alternative products (isoforms)"),
	involvementInDisease: varchar("Involvement in disease"),
	mutagenesis: varchar("Mutagenesis"),
	"subcellularLocation [cc]": varchar("Subcellular location [CC]"),
	postTranslationalModification: varchar("Post-translational modification"),
	pubMedId: varchar("PubMed ID"),
	"function [cc]": varchar("Function [CC]"),
	ensembl: varchar("Ensembl"),
	kegg: varchar("KEGG"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const complexportalEntitiesInBronze = bronze.table("complexportal__entities", {
	complexAc: varchar("Complex ac"),
	recommendedName: varchar("Recommended name"),
	aliasesForComplex: varchar("Aliases for complex"),
	taxonomyIdentifier: varchar("Taxonomy identifier"),
	"identifiers (andStoichiometry)OfMoleculesInComplex": varchar("Identifiers (and stoichiometry) of molecules in complex"),
	evidenceCode: varchar("Evidence Code"),
	experimentalEvidence: varchar("Experimental evidence"),
	goAnnotations: varchar("Go Annotations"),
	crossReferences: varchar("Cross references"),
	description: varchar("Description"),
	complexProperties: varchar("Complex properties"),
	complexAssembly: varchar("Complex assembly"),
	ligand: varchar("Ligand"),
	disease: varchar("Disease"),
	agonist: varchar("Agonist"),
	antagonist: varchar("Antagonist"),
	comment: varchar("Comment"),
	source: varchar("Source"),
	expandedParticipantList: varchar("Expanded participant list"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const geneOntologyCvTermsInBronze = bronze.table("gene_ontology__cv_terms", {
	stanzaType: varchar("_stanza_type"),
	altIds: varchar("alt_ids"),
	comment: varchar(),
	consider: varchar(),
	definition: varchar(),
	definitionReferences: varchar("definition_references"),
	id: varchar(),
	isObsolete: varchar("is_obsolete"),
	name: varchar(),
	namespace: varchar(),
	parentIds: varchar("parent_ids"),
	replacedBy: varchar("replaced_by"),
	synonymTexts: varchar("synonym_texts"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const intactInteractionsInBronze = bronze.table("intact__interactions", {
	"id(s)InteractorA": varchar("ID(s) interactor A"),
	"id(s)InteractorB": varchar("ID(s) interactor B"),
	"altId(s)InteractorA": varchar("Alt. ID(s) interactor A"),
	"altId(s)InteractorB": varchar("Alt. ID(s) interactor B"),
	"alias(es)InteractorA": varchar("Alias(es) interactor A"),
	"alias(es)InteractorB": varchar("Alias(es) interactor B"),
	"interactionDetectionMethod(s)": varchar("Interaction detection method(s)"),
	"publication1StAuthor(s)": varchar("Publication 1st author(s)"),
	"publicationIdentifier(s)": varchar("Publication Identifier(s)"),
	taxidInteractorA: varchar("Taxid interactor A"),
	taxidInteractorB: varchar("Taxid interactor B"),
	"interactionType(s)": varchar("Interaction type(s)"),
	"sourceDatabase(s)": varchar("Source database(s)"),
	"interactionIdentifier(s)": varchar("Interaction identifier(s)"),
	"confidenceValue(s)": varchar("Confidence value(s)"),
	"expansionMethod(s)": varchar("Expansion method(s)"),
	"biologicalRole(s)InteractorA": varchar("Biological role(s) interactor A"),
	"biologicalRole(s)InteractorB": varchar("Biological role(s) interactor B"),
	"experimentalRole(s)InteractorA": varchar("Experimental role(s) interactor A"),
	"experimentalRole(s)InteractorB": varchar("Experimental role(s) interactor B"),
	"type(s)InteractorA": varchar("Type(s) interactor A"),
	"type(s)InteractorB": varchar("Type(s) interactor B"),
	"xref(s)InteractorA": varchar("Xref(s) interactor A"),
	"xref(s)InteractorB": varchar("Xref(s) interactor B"),
	"interactionXref(s)": varchar("Interaction Xref(s)"),
	"annotation(s)InteractorA": varchar("Annotation(s) interactor A"),
	"annotation(s)InteractorB": varchar("Annotation(s) interactor B"),
	"interactionAnnotation(s)": varchar("Interaction annotation(s)"),
	"hostOrganism(s)": varchar("Host organism(s)"),
	"interactionParameter(s)": varchar("Interaction parameter(s)"),
	creationDate: varchar("Creation date"),
	updateDate: varchar("Update date"),
	"checksum(s)InteractorA": varchar("Checksum(s) interactor A"),
	"checksum(s)InteractorB": varchar("Checksum(s) interactor B"),
	"interactionChecksum(s)": varchar("Interaction Checksum(s)"),
	negative: varchar("Negative"),
	"feature(s)InteractorA": varchar("Feature(s) interactor A"),
	"feature(s)InteractorB": varchar("Feature(s) interactor B"),
	"stoichiometry(s)InteractorA": varchar("Stoichiometry(s) interactor A"),
	"stoichiometry(s)InteractorB": varchar("Stoichiometry(s) interactor B"),
	identificationMethodParticipantA: varchar("Identification method participant A"),
	identificationMethodParticipantB: varchar("Identification method participant B"),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const keaInteractionsInBronze = bronze.table("kea__interactions", {
	column0: varchar(),
	column1: varchar(),
	column2: varchar(),
	column3: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const keaKeaEnzymeSubstrateInBronze = bronze.table("kea__kea_enzyme_substrate", {
	column0: varchar(),
	column1: varchar(),
	column2: varchar(),
	column3: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const lrdbLrdbAnnotationsInBronze = bronze.table("lrdb__lrdb_annotations", {
	ligand: varchar(),
	receptor: varchar(),
	ligandName: varchar("ligand.name"),
	receptorName: varchar("receptor.name"),
	ligandSynonyms: varchar("ligand.synonyms"),
	receptorSynonyms: varchar("receptor.synonyms"),
	ligandAlternNames: varchar("ligand.altern.names"),
	receptorAlternNames: varchar("receptor.altern.names"),
	source: varchar(),
	pmiDs: varchar("PMIDs"),
	cellsL: varchar("cells.L"),
	cellsR: varchar("cells.R"),
	remarks: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const lrdbAnnotationsInBronze = bronze.table("lrdb__annotations", {
	ligand: varchar(),
	receptor: varchar(),
	ligandName: varchar("ligand.name"),
	receptorName: varchar("receptor.name"),
	ligandSynonyms: varchar("ligand.synonyms"),
	receptorSynonyms: varchar("receptor.synonyms"),
	ligandAlternNames: varchar("ligand.altern.names"),
	receptorAlternNames: varchar("receptor.altern.names"),
	source: varchar(),
	pmiDs: varchar("PMIDs"),
	cellsL: varchar("cells.L"),
	cellsR: varchar("cells.R"),
	remarks: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const lrdbLrdbInteractionsInBronze = bronze.table("lrdb__lrdb_interactions", {
	ligand: varchar(),
	receptor: varchar(),
	ligandName: varchar("ligand.name"),
	receptorName: varchar("receptor.name"),
	ligandSynonyms: varchar("ligand.synonyms"),
	receptorSynonyms: varchar("receptor.synonyms"),
	ligandAlternNames: varchar("ligand.altern.names"),
	receptorAlternNames: varchar("receptor.altern.names"),
	source: varchar(),
	pmiDs: varchar("PMIDs"),
	cellsL: varchar("cells.L"),
	cellsR: varchar("cells.R"),
	remarks: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const cellchatdbCellchatdbInteractionsInBronze = bronze.table("cellchatdb__cellchatdb_interactions", {
	interactionName: varchar("interaction_name"),
	pathwayName: varchar("pathway_name"),
	ligand: varchar(),
	receptor: varchar(),
	agonist: varchar(),
	antagonist: varchar(),
	coAReceptor: varchar("co_A_receptor"),
	coIReceptor: varchar("co_I_receptor"),
	annotation: varchar(),
	interactionName2: varchar("interaction_name_2"),
	evidence: varchar(),
	isNeurotransmitter: varchar("is_neurotransmitter"),
	ligandSymbol: varchar("ligand.symbol"),
	ligandFamily: varchar("ligand.family"),
	ligandLocation: varchar("ligand.location"),
	ligandKeyword: varchar("ligand.keyword"),
	ligandSecretedType: varchar("ligand.secreted_type"),
	ligandTransmembrane: varchar("ligand.transmembrane"),
	receptorSymbol: varchar("receptor.symbol"),
	receptorFamily: varchar("receptor.family"),
	receptorLocation: varchar("receptor.location"),
	receptorKeyword: varchar("receptor.keyword"),
	receptorSurfaceomeMain: varchar("receptor.surfaceome_main"),
	receptorSurfaceomeSub: varchar("receptor.surfaceome_sub"),
	receptorAdhesome: varchar("receptor.adhesome"),
	receptorSecretedType: varchar("receptor.secreted_type"),
	receptorTransmembrane: varchar("receptor.transmembrane"),
	version: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const cellchatdbCellchatdbComplexesInBronze = bronze.table("cellchatdb__cellchatdb_complexes", {
	interactionName: varchar("interaction_name"),
	pathwayName: varchar("pathway_name"),
	ligand: varchar(),
	receptor: varchar(),
	agonist: varchar(),
	antagonist: varchar(),
	coAReceptor: varchar("co_A_receptor"),
	coIReceptor: varchar("co_I_receptor"),
	annotation: varchar(),
	interactionName2: varchar("interaction_name_2"),
	evidence: varchar(),
	isNeurotransmitter: varchar("is_neurotransmitter"),
	ligandSymbol: varchar("ligand.symbol"),
	ligandFamily: varchar("ligand.family"),
	ligandLocation: varchar("ligand.location"),
	ligandKeyword: varchar("ligand.keyword"),
	ligandSecretedType: varchar("ligand.secreted_type"),
	ligandTransmembrane: varchar("ligand.transmembrane"),
	receptorSymbol: varchar("receptor.symbol"),
	receptorFamily: varchar("receptor.family"),
	receptorLocation: varchar("receptor.location"),
	receptorKeyword: varchar("receptor.keyword"),
	receptorSurfaceomeMain: varchar("receptor.surfaceome_main"),
	receptorSurfaceomeSub: varchar("receptor.surfaceome_sub"),
	receptorAdhesome: varchar("receptor.adhesome"),
	receptorSecretedType: varchar("receptor.secreted_type"),
	receptorTransmembrane: varchar("receptor.transmembrane"),
	version: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const cellchatdbCellchatdbAnnotationsInBronze = bronze.table("cellchatdb__cellchatdb_annotations", {
	interactionName: varchar("interaction_name"),
	pathwayName: varchar("pathway_name"),
	ligand: varchar(),
	receptor: varchar(),
	agonist: varchar(),
	antagonist: varchar(),
	coAReceptor: varchar("co_A_receptor"),
	coIReceptor: varchar("co_I_receptor"),
	annotation: varchar(),
	interactionName2: varchar("interaction_name_2"),
	evidence: varchar(),
	isNeurotransmitter: varchar("is_neurotransmitter"),
	ligandSymbol: varchar("ligand.symbol"),
	ligandFamily: varchar("ligand.family"),
	ligandLocation: varchar("ligand.location"),
	ligandKeyword: varchar("ligand.keyword"),
	ligandSecretedType: varchar("ligand.secreted_type"),
	ligandTransmembrane: varchar("ligand.transmembrane"),
	receptorSymbol: varchar("receptor.symbol"),
	receptorFamily: varchar("receptor.family"),
	receptorLocation: varchar("receptor.location"),
	receptorKeyword: varchar("receptor.keyword"),
	receptorSurfaceomeMain: varchar("receptor.surfaceome_main"),
	receptorSurfaceomeSub: varchar("receptor.surfaceome_sub"),
	receptorAdhesome: varchar("receptor.adhesome"),
	receptorSecretedType: varchar("receptor.secreted_type"),
	receptorTransmembrane: varchar("receptor.transmembrane"),
	version: varchar(),
	metadataResource: varchar("metadata_resource"),
	metadataDataset: varchar("metadata_dataset"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const cellchatdbCellchatdbInBronze = bronze.table("cellchatdb__cellchatdb", {
	interactionName: varchar("interaction_name"),
	pathwayName: varchar("pathway_name"),
	ligand: varchar(),
	receptor: varchar(),
	agonist: varchar(),
	antagonist: varchar(),
	coAReceptor: varchar("co_A_receptor"),
	coIReceptor: varchar("co_I_receptor"),
	annotation: varchar(),
	interactionName2: varchar("interaction_name_2"),
	evidence: varchar(),
	isNeurotransmitter: varchar("is_neurotransmitter"),
	ligandSymbol: varchar("ligand.symbol"),
	ligandFamily: varchar("ligand.family"),
	ligandLocation: varchar("ligand.location"),
	ligandKeyword: varchar("ligand.keyword"),
	ligandSecretedType: varchar("ligand.secreted_type"),
	ligandTransmembrane: varchar("ligand.transmembrane"),
	receptorSymbol: varchar("receptor.symbol"),
	receptorFamily: varchar("receptor.family"),
	receptorLocation: varchar("receptor.location"),
	receptorKeyword: varchar("receptor.keyword"),
	receptorSurfaceomeMain: varchar("receptor.surfaceome_main"),
	receptorSurfaceomeSub: varchar("receptor.surfaceome_sub"),
	receptorAdhesome: varchar("receptor.adhesome"),
	receptorSecretedType: varchar("receptor.secreted_type"),
	receptorTransmembrane: varchar("receptor.transmembrane"),
	version: varchar(),
	metadataSource: varchar("metadata_source"),
	metadataLoadedAt: varchar("metadata_loaded_at"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	metadataRowNumber: bigint("metadata_row_number", { mode: "number" }),
});

export const cvNamespaceInGold = gold.table("cv_namespace", {
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	id: bigint({ mode: "number" }),
	name: varchar(),
	description: varchar(),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	referenceId: bigint("reference_id", { mode: "number" }),
});

export const referenceInGold = gold.table("reference", {
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	id: bigint({ mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	pubmedId: bigint("pubmed_id", { mode: "number" }),
	doi: integer(),
});

export const cvTermInGold = gold.table("cv_term", {
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	id: bigint({ mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	namespaceId: bigint("namespace_id", { mode: "number" }),
	accession: varchar(),
	name: varchar(),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	categoryId: bigint("category_id", { mode: "number" }),
	definition: varchar(),
	isObsolete: boolean("is_obsolete"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	replacedById: bigint("replaced_by_id", { mode: "number" }),
	comment: varchar(),
});

export const cvTermHierarchyInGold = gold.table("cv_term_hierarchy", {
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	id: bigint({ mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	parentId: bigint("parent_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	childId: bigint("child_id", { mode: "number" }),
});

export const entityInGold = gold.table("entity", {
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	id: bigint({ mode: "number" }),
	canonicalIdentifier: varchar("canonical_identifier"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	canonicalIdentifierTypeId: bigint("canonical_identifier_type_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	entityTypeId: bigint("entity_type_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	ncbiTaxIdId: bigint("ncbi_tax_id_id", { mode: "number" }),
	description: varchar(),
	altId: varchar("alt_id"),
});

export const entityIdentifierInGold = gold.table("entity_identifier", {
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	id: bigint({ mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	entityId: bigint("entity_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	identifierTypeId: bigint("identifier_type_id", { mode: "number" }),
	value: varchar(),
});

export const entityMembershipInGold = gold.table("entity_membership", {
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	id: bigint({ mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	parentEntityId: bigint("parent_entity_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	memberEntityId: bigint("member_entity_id", { mode: "number" }),
	stoichiometry: integer(),
});

export const proteinDetailsInGold = gold.table("protein_details", {
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	id: bigint({ mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	entityId: bigint("entity_id", { mode: "number" }),
	sequenceLength: integer("sequence_length"),
	molecularWeight: integer("molecular_weight"),
});

export const interactionCanonicalInGold = gold.table("interaction_canonical", {
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	id: bigint({ mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	entityAId: bigint("entity_a_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	entityBId: bigint("entity_b_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	evidenceCount: bigint("evidence_count", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	sourceCount: bigint("source_count", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	publicationCount: bigint("publication_count", { mode: "number" }),
	dataSources: varchar("data_sources"),
	interactionTypes: varchar("interaction_types"),
	hasDirectedEvidence: boolean("has_directed_evidence"),
});

export const interactionEvidenceInGold = gold.table("interaction_evidence", {
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	id: bigint({ mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	interactionId: bigint("interaction_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	dataSourceId: bigint("data_source_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	referenceId: bigint("reference_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	interactionTypeId: bigint("interaction_type_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	causalMechanismId: bigint("causal_mechanism_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	causalStatementId: bigint("causal_statement_id", { mode: "number" }),
	evidenceSentence: varchar("evidence_sentence"),
	sourceIdentifier: varchar("source_identifier"),
	isDirected: boolean("is_directed"),
	direction: varchar(),
	sign: varchar(),
});

export const entityInteractionStatsInGold = gold.table("entity_interaction_stats", {
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	id: bigint({ mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	entityId: bigint("entity_id", { mode: "number" }),
	canonicalIdentifier: varchar("canonical_identifier"),
	entityType: varchar("entity_type"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	interactionCount: bigint("interaction_count", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	partnerCount: bigint("partner_count", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	sourceCount: bigint("source_count", { mode: "number" }),
	hubCategory: varchar("hub_category"),
});

export const networkMetricsInGold = gold.table("network_metrics", {
	id: integer(),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	totalEntities: bigint("total_entities", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	proteinCount: bigint("protein_count", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	complexCount: bigint("complex_count", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	smallMoleculeCount: bigint("small_molecule_count", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	geneCount: bigint("gene_count", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	totalInteractions: bigint("total_interactions", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	directedInteractions: bigint("directed_interactions", { mode: "number" }),
	avgEvidencePerInteraction: doublePrecision("avg_evidence_per_interaction"),
	avgSourcesPerInteraction: doublePrecision("avg_sources_per_interaction"),
	avgPublicationsPerInteraction: doublePrecision("avg_publications_per_interaction"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	totalDataSources: bigint("total_data_sources", { mode: "number" }),
	dataSourcesList: varchar("data_sources_list"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	totalReferences: bigint("total_references", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	entitiesWithoutInteractions: bigint("entities_without_interactions", { mode: "number" }),
	percentEntitiesWithoutInteractions: doublePrecision("percent_entities_without_interactions"),
	avgDegree: doublePrecision("avg_degree"),
	lastUpdated: timestamp("last_updated", { withTimezone: true, mode: 'string' }),
});

export const dataQualityMetricsInGold = gold.table("data_quality_metrics", {
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	id: bigint({ mode: "number" }),
	metric: varchar(),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	count: bigint({ mode: "number" }),
	percentage: doublePrecision(),
	severity: varchar(),
	lastUpdated: timestamp("last_updated", { withTimezone: true, mode: 'string' }),
});

export const goldMeilisearchEntitiesInGold = gold.table("gold_meilisearch_entities", {
	id: varchar(),
	type: varchar(),
	geneSymbol: varchar("gene_symbol"),
	canonicalIdentifier: varchar("canonical_identifier"),
	allIdentifiers: varchar("all_identifiers").array(),
	entityTypeName: varchar("entity_type_name"),
	ncbiTaxName: varchar("ncbi_tax_name"),
	description: varchar(),
	cvTermIds: varchar("cv_term_ids").array(),
	interactionIds: varchar("interaction_ids").array(),
});

export const goldMeilisearchCvTermsInGold = gold.table("gold_meilisearch_cv_terms", {
	id: varchar(),
	type: varchar(),
	name: varchar(),
	synonyms: varchar().array(),
	namespace: varchar(),
	definition: varchar(),
	associatedEntityIds: varchar("associated_entity_ids").array(),
	directParentIds: varchar("direct_parent_ids").array(),
	categoryId: varchar("category_id"),
});

export const goldMeilisearchInteractionsInGold = gold.table("gold_meilisearch_interactions", {
	id: varchar(),
	type: varchar(),
	entityIds: varchar("entity_ids").array(),
	entityACanonicalId: varchar("entity_a_canonical_id"),
	entityBCanonicalId: varchar("entity_b_canonical_id"),
	entityAName: varchar("entity_a_name"),
	entityBName: varchar("entity_b_name"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	evidenceCount: bigint("evidence_count", { mode: "number" }),
	interactionTypeIds: varchar("interaction_type_ids").array(),
	interactionTypeNames: varchar("interaction_type_names").array(),
	dataSourceIds: varchar("data_source_ids").array(),
	dataSourceNames: varchar("data_source_names").array(),
	causalStatementIds: varchar("causal_statement_ids").array(),
	causalStatementNames: varchar("causal_statement_names").array(),
	causalMechanismIds: varchar("causal_mechanism_ids").array(),
	causalMechanismNames: varchar("causal_mechanism_names").array(),
	detectionMethodIds: varchar("detection_method_ids").array(),
	detectionMethodNames: varchar("detection_method_names").array(),
	interactorTypeIds: varchar("interactor_type_ids").array(),
	interactorTypeNames: varchar("interactor_type_names").array(),
	interactionTypesFacet: varchar("interaction_types_facet").array(),
	dataSourcesFacet: varchar("data_sources_facet").array(),
	causalStatementsFacet: varchar("causal_statements_facet").array(),
	causalMechanismsFacet: varchar("causal_mechanisms_facet").array(),
	detectionMethodsFacet: varchar("detection_methods_facet").array(),
	interactorTypesFacet: varchar("interactor_types_facet").array(),
	signs: varchar().array(),
	consensusSign: varchar("consensus_sign"),
	isDirected: boolean("is_directed"),
	consensusDirection: varchar("consensus_direction"),
});
