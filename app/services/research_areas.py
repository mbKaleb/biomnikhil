"""The research areas the About Biomni panel sorts everything into.

Biomni lists its tools by its own modules ("genomics", "pharmacology") and its
data lake as one flat list of file names. A researcher thinks in questions
instead: what can it tell me about a gene, a drug, a cell type? So each tool,
data file and piece of software is given a main area here, by plain tables, plus any other areas it also
serves (a protein-interaction table is a pathways file and a proteins file).

Anything biomni adds later that isn't in a table lands in GENERAL, so nothing
disappears from the panel; tests/test_about.py keeps the tables complete for
the biomni version we pin.
"""
from __future__ import annotations

GENES = "genes"
PROTEINS = "proteins"
DRUGS = "drugs"
EXPRESSION = "expression"
PATHWAYS = "pathways"
REGULATION = "regulation"
CANCER = "cancer"
IMMUNE_MICRO = "immune-micro"
LAB_DESIGN = "lab-design"
IMAGING = "imaging"
LITERATURE = "literature"
GENERAL = "general"

# In the order the panel shows them. `summary` says what the area is for and
# `example` is a question to start from (the panel can drop it in the message box).
AREAS = [
    {
        "id": GENES,
        "title": "Genes, variants & sequencing",
        "summary": "Look up a gene, a variant or a GWAS hit, find the genes behind a disease, and process sequencing reads or variant files.",
        "example": "Which genes are most strongly linked to Parkinson's disease? For the top five, summarize their known pathogenic variants.",
    },
    {
        "id": PROTEINS,
        "title": "Proteins, RNA & structure",
        "summary": "Sequence, domains, 3D structure, folding and conservation of a protein or an RNA.",
        "example": "Find the domains and the best available 3D structure of human TP53, and list its disordered regions.",
    },
    {
        "id": DRUGS,
        "title": "Drugs, targets & safety",
        "summary": "Find compounds for a target, check binding, interactions, ADMET and safety, or look for a drug to repurpose.",
        "example": "Which approved drugs target EGFR? Check which of them interact with each other and list the FDA adverse events reported for the top two.",
    },
    {
        "id": EXPRESSION,
        "title": "Expression & single-cell",
        "summary": "Where a gene is expressed, which cell types are in a sample, and what changes between conditions.",
        "example": "In which tissues is SCN5A most highly expressed (GTEx)? Then list marker genes for the cardiac cell types.",
    },
    {
        "id": PATHWAYS,
        "title": "Pathways, networks & metabolism",
        "summary": "Protein interactions, gene sets, enrichment, signalling and metabolic models.",
        "example": "Run pathway enrichment on the gene list in my attached file using GO and Reactome, and show which of the proteins interact.",
    },
    {
        "id": REGULATION,
        "title": "Gene regulation & epigenomics",
        "summary": "Transcription factors, regulatory elements, miRNA targets, ChIP-seq and ATAC-seq peaks, chromatin contacts.",
        "example": "Which transcription factors are predicted to bind near the MYC promoter, and which miRNAs target MYC?",
    },
    {
        "id": CANCER,
        "title": "Cancer & functional screens",
        "summary": "Cell-line dependencies, synthetic lethality, CRISPR screens, somatic mutations and copy number.",
        "example": "Using DepMap, which genes are selectively essential in KRAS-mutant cell lines, and are any known synthetic lethal partners of KRAS?",
    },
    {
        "id": IMMUNE_MICRO,
        "title": "Immunology & microbiology",
        "summary": "T-cell receptors, virus-host interactions, immune cell assays, bacterial growth and genomes.",
        "example": "Find T-cell receptors known to recognize EBV and summarize which epitopes they target.",
    },
    {
        "id": LAB_DESIGN,
        "title": "Lab design: editing, cloning & automation",
        "summary": "Guide RNAs, primers, restriction digests, plasmid assembly, protocols and liquid-handling robots.",
        "example": "Design three sgRNAs to knock out mouse Trp53 and two primer pairs to verify the edit.",
    },
    {
        "id": IMAGING,
        "title": "Imaging, assays & physiology",
        "summary": "Microscopy segmentation and registration, flow cytometry, histology, blots and physiological measurements.",
        "example": "Segment the cells in my attached microscopy image and report cell count and size distribution.",
    },
    {
        "id": LITERATURE,
        "title": "Literature & clinical evidence",
        "summary": "Search papers, read a PDF or a web page, find clinical trials, survival analysis.",
        "example": "Search PubMed for recent trials of GLP-1 agonists in Alzheimer's disease and list the ongoing ones on ClinicalTrials.gov.",
    },
    {
        "id": GENERAL,
        "title": "General analysis & other sources",
        "summary": "Statistics, machine learning, plotting, file formats, and databases outside human biomedicine.",
        "example": "Load my attached CSV, compare the two groups, and make a volcano plot.",
    },
]

AREA_IDS = []
for _area in AREAS:
    AREA_IDS.append(_area["id"])

# ---- tools ------------------------------------------------------------------

# Where a tool goes unless TOOL_AREA_OVERRIDES says otherwise, by biomni module.
TOOL_FIELD_AREAS = {
    "literature": LITERATURE,
    "biochemistry": PROTEINS,
    "bioimaging": IMAGING,
    "bioengineering": IMAGING,
    "biophysics": PROTEINS,
    "glycoengineering": PROTEINS,
    "cancer_biology": CANCER,
    "cell_biology": IMAGING,
    "molecular_biology": LAB_DESIGN,
    "genetics": GENES,
    "genomics": EXPRESSION,
    "immunology": IMMUNE_MICRO,
    "microbiology": IMMUNE_MICRO,
    "pathology": IMAGING,
    "pharmacology": DRUGS,
    "physiology": IMAGING,
    "synthetic_biology": LAB_DESIGN,
    "systems_biology": PATHWAYS,
    "support_tools": GENERAL,
    "lab_automation": LAB_DESIGN,
    "database": GENERAL,
}

TOOL_AREA_OVERRIDES = {
    # bioengineering
    "perform_crispr_cas9_genome_editing": LAB_DESIGN,
    "analyze_in_vitro_drug_release_kinetics": DRUGS,
    "simulate_whole_cell_ode_model": PATHWAYS,
    # biophysics
    "analyze_cell_morphology_and_cytoskeleton": IMAGING,
    "analyze_tissue_deformation_flow": IMAGING,
    # genetics
    "analyze_cas9_mutation_outcomes": LAB_DESIGN,
    "analyze_crispr_genome_editing": LAB_DESIGN,
    "identify_transcription_factor_binding_sites": REGULATION,
    "perform_pcr_and_gel_electrophoresis": LAB_DESIGN,
    "analyze_protein_phylogeny": PROTEINS,
    # genomics
    "gene_set_enrichment_analysis": PATHWAYS,
    "get_gene_set_enrichment_analysis_supported_database_list": PATHWAYS,
    "analyze_chromatin_interactions": REGULATION,
    "perform_chipseq_peak_calling_with_macs2": REGULATION,
    "find_enriched_motifs_with_homer": REGULATION,
    "analyze_genomic_region_overlap": REGULATION,
    "analyze_comparative_genomics_and_haplotypes": GENES,
    "interspecies_gene_conversion": GENES,
    "generate_gene_embeddings_with_ESM_models": PROTEINS,
    # immunology
    "analyze_atac_seq_differential_accessibility": REGULATION,
    "analyze_cns_lesion_histology": IMAGING,
    "analyze_immunohistochemistry_image": IMAGING,
    # microbiology
    "segment_and_analyze_microbial_cells": IMAGING,
    "segment_cells_with_deep_learning": IMAGING,
    "predict_rna_secondary_structure": PROTEINS,
    "analyze_arsenic_speciation_hplc_icpms": IMAGING,
    # pharmacology
    "analyze_western_blot": IMAGING,
    "run_3d_chondrogenic_aggregate_assay": IMAGING,
    "analyze_xenograft_tumor_growth_inhibition": CANCER,
    "estimate_alpha_particle_radiotherapy_dosimetry": CANCER,
    "perform_mwas_cyp2c19_metabolizer_status": GENES,
    # physiology
    "simulate_thyroid_hormone_pharmacokinetics": DRUGS,
    # synthetic biology
    "analyze_bifurcation_diagram": PATHWAYS,
    "create_biochemical_network_sbml_model": PATHWAYS,
    "simulate_gene_circuit_with_growth_feedback": PATHWAYS,
    "identify_fas_functional_domains": PROTEINS,
    # systems biology
    "compare_protein_structures": PROTEINS,
    "query_chatnt": REGULATION,
    # the clinical-trials lookup is a database tool, but it is evidence, not data
    "query_clinicaltrials": LITERATURE,
}

# The live database lookups (biomni's "database" module), by name.
DATABASE_TOOL_AREAS = {
    "query_clinvar": GENES,
    "query_dbsnp": GENES,
    "query_gnomad": GENES,
    "query_gwas_catalog": GENES,
    "query_ensembl": GENES,
    "query_ucsc": GENES,
    "query_monarch": GENES,
    "query_mpd": GENES,
    "query_uniprot": PROTEINS,
    "query_alphafold": PROTEINS,
    "query_interpro": PROTEINS,
    "query_pdb": PROTEINS,
    "query_pdb_identifiers": PROTEINS,
    "query_emdb": PROTEINS,
    "blast_sequence": PROTEINS,
    "query_chembl": DRUGS,
    "query_pubchem": DRUGS,
    "query_unichem": DRUGS,
    "query_gtopdb": DRUGS,
    "query_openfda": DRUGS,
    "query_dailymed": DRUGS,
    "query_opentarget": DRUGS,
    "query_geo": EXPRESSION,
    "query_pride": EXPRESSION,
    "query_kegg": PATHWAYS,
    "query_reactome": PATHWAYS,
    "query_stringdb": PATHWAYS,
    "query_quickgo": PATHWAYS,
    "query_jaspar": REGULATION,
    "query_remap": REGULATION,
    "query_regulomedb": REGULATION,
    "query_encode": REGULATION,
    "region_to_ccre_screen": REGULATION,
    "get_genes_near_ccre": REGULATION,
    "query_cbioportal": CANCER,
    "query_clinicaltrials": LITERATURE,
    "query_synapse": GENERAL,
    "query_iucn": GENERAL,
    "query_paleobiology": GENERAL,
    "query_worms": GENERAL,
}


# Tools that also help in another area than their main one.
TOOL_ALSO_AREAS = {
    "query_stringdb": [PROTEINS],
    "query_pride": [PROTEINS],
    "query_gtopdb": [PROTEINS],
    "query_quickgo": [PROTEINS],
    "predict_binding_affinity_protein_1d_sequence": [PROTEINS],
    "run_diffdock_with_smiles": [PROTEINS],
    "docking_autodock_vina": [PROTEINS],
    "run_autosite": [PROTEINS],
    "analyze_protein_colocalization": [PROTEINS],
    "analyze_western_blot": [PROTEINS],
    "model_protein_dimerization_network": [PROTEINS],
    "simulate_protein_signaling_network": [PROTEINS],
}


def tool_area(field: str, name: str) -> str:
    if name in TOOL_AREA_OVERRIDES:
        return TOOL_AREA_OVERRIDES[name]
    if field == "database":
        return DATABASE_TOOL_AREAS.get(name, GENERAL)
    return TOOL_FIELD_AREAS.get(field, GENERAL)


def tool_areas(field: str, name: str) -> list[str]:
    """The tool's main area first, then the others it also serves."""
    areas = [tool_area(field, name)]
    for extra_area in TOOL_ALSO_AREAS.get(name, []):
        if extra_area not in areas:
            areas.append(extra_area)
    return areas


# ---- data lake files --------------------------------------------------------

# First prefix that a file name starts with (ignoring case) wins. The first
# area listed is the file's main area; any others are where it also helps.
LAKE_PREFIX_AREAS = [
    ("affinity_capture", [PATHWAYS, PROTEINS]),
    ("co-fractionation", [PATHWAYS, PROTEINS]),
    ("proximity_label", [PATHWAYS, PROTEINS]),
    ("reconstituted_complex", [PATHWAYS, PROTEINS]),
    ("two-hybrid", [PATHWAYS, PROTEINS]),
    ("msigdb_", [PATHWAYS]),
    ("mousemine_", [PATHWAYS]),
    ("go-plus", [PATHWAYS]),
    ("kg.csv", [PATHWAYS]),
    ("virus-host", [IMMUNE_MICRO, PROTEINS]),
    ("mcpas", [IMMUNE_MICRO]),
    ("depmap_", [CANCER]),
    ("synthetic_", [CANCER]),
    ("genetic_interaction", [CANCER]),
    ("dosage_growth", [CANCER]),
    ("sgrna_ko", [LAB_DESIGN]),
    ("mirdb", [REGULATION]),
    ("mirtarbase", [REGULATION]),
    ("bindingdb", [DRUGS, PROTEINS]),
    ("broad_repurposing", [DRUGS]),
    ("ddinter", [DRUGS]),
    ("enamine", [DRUGS]),
    ("evebio", [DRUGS]),
    ("txgnn", [DRUGS]),
    ("gtex", [EXPRESSION]),
    ("proteinatlas", [EXPRESSION, PROTEINS]),
    ("czi_census", [EXPRESSION]),
    ("marker_celltype", [EXPRESSION]),
    ("gene_info", [GENES]),
    ("variant_table", [GENES]),
    ("gwas_catalog", [GENES]),
    ("genebass", [GENES]),
    ("omim", [GENES]),
    ("disgenet", [GENES]),
    ("hp.obo", [GENES]),
]


def lake_file_areas(name: str) -> list[str]:
    """The file's main area first, then the others it also serves."""
    lowered = name.lower()
    for prefix, areas in LAKE_PREFIX_AREAS:
        if lowered.startswith(prefix):
            return list(areas)
    return [GENERAL]


# ---- software ---------------------------------------------------------------

SOFTWARE_BY_AREA = {
    GENES: [
        "biopython", "scikit-bio", "gget", "pyliftover", "pysam", "pyfaidx", "pyranges",
        "pybedtools", "cyvcf2", "msprime", "tskit", "plink", "plink2", "gcta64",
        "samtools", "bowtie2", "bwa", "bedtools", "fastqc", "trimmomatic",
    ],
    PROTEINS: [
        "biotite", "biopandas", "pdbfixer", "openmm", "viennarna", "cryosparc-tools",
        "mafft", "muscle", "FastTree", "iqtree2", "diamond",
    ],
    DRUGS: [
        "rdkit", "deeppurpose", "pyscreener", "openbabel", "descriptastorus", "pytdc",
        "ADFR", "vina", "autosite",
    ],
    EXPRESSION: [
        "scanpy", "anndata", "mudata", "scrublet", "scvelo", "cellxgene-census", "loompy",
        "harmony-pytorch", "tiledb", "tiledbsoma", "DESeq2", "edgeR", "limma", "harmony",
        "Matrix",
    ],
    PATHWAYS: [
        "gseapy", "clusterProfiler", "WGCNA", "igraph", "cobra", "python-libsbml", "optlang",
    ],
    REGULATION: [
        "macs2", "Homer", "pyBigWig", "cooler", "fanc", "pyscenic", "arboreto",
    ],
    CANCER: ["mageck"],
    IMMUNE_MICRO: ["biom-format"],
    LAB_DESIGN: ["plannotate", "PyLabRobot"],
    IMAGING: [
        "opencv-python", "scikit-image", "cellpose", "nnunet", "trackpy", "lazyslide",
        "pymzml", "PyMassSpec", "FlowIO", "FlowUtils", "flowkit", "fcsparser",
    ],
    LITERATURE: [
        "pymed", "arxiv", "scholarly", "googlesearch-python", "PyPDF2", "lifelines",
    ],
    GENERAL: [
        "pandas", "numpy", "scipy", "scikit-learn", "matplotlib", "seaborn", "statsmodels",
        "pymc3", "umap-learn", "faiss-cpu", "hyperopt", "h5py", "tqdm", "joblib",
        "reportlab", "pykalman", "hmmlearn", "ggplot2", "dplyr", "tidyr", "readr", "stringr",
    ],
}

_software_area_by_name = {}
for _area_id, _names in SOFTWARE_BY_AREA.items():
    for _software_name in _names:
        _software_area_by_name[_software_name] = _area_id


# Software that also helps in another area than its main one.
SOFTWARE_ALSO_AREAS = {
    "biopython": [PROTEINS],
    "deeppurpose": [PROTEINS],
    "vina": [PROTEINS],
    "ADFR": [PROTEINS],
    "autosite": [PROTEINS],
    "pymzml": [PROTEINS],
    "PyMassSpec": [PROTEINS],
}


def software_area(name: str) -> str:
    return _software_area_by_name.get(name, GENERAL)


def software_areas(name: str) -> list[str]:
    """The package's main area first, then the others it also serves."""
    areas = [software_area(name)]
    for extra_area in SOFTWARE_ALSO_AREAS.get(name, []):
        if extra_area not in areas:
            areas.append(extra_area)
    return areas
