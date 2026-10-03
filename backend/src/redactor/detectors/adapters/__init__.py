"""Candidate detector adapters (plan §11 C1): Presidio, OpenRedaction, Philter. Each maps its native labels
to the taxonomy through a versioned label map in config/adapters/; unmapped labels become OTHER and are
counted. All run locally with no network (Presidio in-process under the network guard, OpenRedaction in a
Node sidecar with an in-process guard, Philter in a container with --network none)."""
