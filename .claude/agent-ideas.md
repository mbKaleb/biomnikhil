# Agent ideas

Ideas only; none of these are built. Build in this order.

## 1. Planted-Truth Assay

**Question:** Would our analysis catch a real effect?

The agent copies the lab's real dataset and changes it in ways it keeps track of. For example, it makes 50 genes exactly 1.5x higher in the treated samples, and in another copy shuffles the sample labels so that no real effect should exist. It then runs the normal analysis on each copy.

**Output:** The effect sizes the analysis can detect, and how many hits it reports when nothing is there.

**Limit:** It can show that an analysis is unreliable, but it can't prove that a specific hit is real.

## 2. Cross-Omics Discordance Census

**Question:** Where do the data layers disagree?

The agent finds cases where layers measured on the same samples disagree, such as RNA doubling while protein stays flat. It tries to explain each one away with causes like a sample swap, a batch effect, a protein too scarce to measure, or a protein that lags behind its RNA.

**Output:** A short list of surviving disagreements, what was ruled out for each, and a suggested follow-up experiment.

**Limit:** Survivors are only leads, and the list could still be mostly noise. It needs multiple layers from the same samples, which hasn't been confirmed for this lab.

## 3. Fork Arbitration

**Question:** Does this hit depend on how we analyzed it?

The agent logs analysis choices such as normalization, batch correction and filter cutoffs, including choices it made after seeing results. It reruns the analysis with reasonable alternatives and scores each version against known controls in the data.

**Output:** Which choices each hit depends on, and the check that would settle it, such as qPCR.

**Limit:** It can't catch an artifact that shows up in every version. Without good controls, it can show that a hit is fragile but not which version is right. Drop it if it changes no hit calls across several real datasets.
