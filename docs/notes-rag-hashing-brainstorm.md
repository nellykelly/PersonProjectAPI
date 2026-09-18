# Brainstorm log: hashing, meaning, and fast retrieval

A record of the actual train of thought from one conversation working
through "can hashing solve fast semantic search," idea by idea -- not a
rehearsed answer. The polished, interview-ready version of the same ground
(with code references into this repo's own `/assistant`) lives in
`docs/INTERVIEW-NOTES.md`, section J. This file is the log of how the
reasoning actually got there, kept because the progression itself is worth
remembering, not just the conclusion.

---

## 1. "Hash every document, search finds the closest hash"

The starting idea: embed every document to a vector, compare a question
against every one, take the best match. Correctly identified as too slow
at scale -- a full O(n) scan.

## 2. "Build a graph, big jumps at the top, smaller hops lower down"

This is HNSW (Hierarchical Navigable Small World graphs): a multi-layer
graph, sparse long-range connections up top, dense short-range connections
at the bottom. Search greedily hops toward the query, coarse to fine,
touching a few hundred nodes instead of all n. Real, standard, and it's
the technique behind pgvector's `hnsw` index type.

## 3. "What if we hashed every token uniquely, then searched by finding similar hashes?"

Real technique: **Locality-Sensitive Hashing (LSH)**. The catch that makes
or breaks this idea: a normal hash function (SHA-256, a dictionary's
`hash()`) is *designed* so similar inputs produce unrelated outputs --
the opposite of what's needed here. LSH is a different kind of hash
function, built on purpose so similar vectors *are* likely to land in the
same bucket (e.g. random-hyperplane LSH: pick random hyperplanes, hash =
which side of each one a vector falls on). Same goal as HNSW, different
mechanism -- bucketing vs. graph traversal.

## 4. "Layer the LSH the same way -- 3 layers of big jumps"

Real and it generalizes past LSH specifically:
- **LSH Forest** -- multiple hash tables organized as prefix trees; a
  short prefix match is the coarse "big jump," more bits refine it.
- **IVF** (Inverted File index -- pgvector's *other* index type,
  `ivfflat`) -- same coarse-to-fine shape via k-means clustering instead
  of hashing: jump to the nearest cluster, fine-search only inside it.
- At real billion-scale, production systems (FAISS, underneath most
  vector databases) often stack these: IVF for the coarse jump, HNSW or
  product quantization for the fine refinement within that cell. The
  "big jump, then refine" shape is bigger than any one of these
  techniques -- it's the load-bearing idea behind large-scale ANN in
  general.

## 5. "Hash every word AND the idea, compare how many hashes match"

Two different things bundled together here, and separating them was the
actual insight:
- Hashing many words/shingles and counting matching hashes between two
  documents is **MinHash**, a real technique for estimating *Jaccard
  similarity* (literal token overlap) -- used in Google's original
  web-scale near-duplicate detection.
- The catch: word overlap isn't meaning overlap. "The dog is happy" and
  "the puppy seems glad" share almost no tokens, so MinHash scores them
  as dissimilar despite meaning nearly the same thing. Hashing *words*
  (MinHash) is lexical; hashing an *embedding* (LSH) is semantic --
  different questions, not competing answers to the same one.
- Which is why production RAG generally runs **both** at once: hybrid
  search, lexical (BM25/MinHash-style) plus semantic (embeddings),
  merged and re-ranked. Elasticsearch, Weaviate, and Pinecone all ship
  this as a named mode.

## 6. "Run a converter over the input first -- distill meaning into hashes, then tokenize"

Two separate problems here:
- **The ordering as stated doesn't work.** Tokenizing has to come
  *first* in any real pipeline -- a hash is already a fixed-size,
  structureless number by the time it exists; there's no linguistic
  structure left to tokenize.
- **The real question underneath: can hashing replace the expensive
  embedding step, not just speed up search after it?** No. There are two
  separate costs in a RAG query -- embedding the question (a trained
  model's forward pass, genuinely expensive) and searching the index
  (what HNSW/LSH/IVF all speed up, comparatively cheap). LSH doesn't
  *create* meaning; it buckets a vector a neural network already
  produced. Hash raw tokens directly and there's no semantic signal left
  to preserve -- back to MinHash territory.
- **The one place "hash the input for speed" genuinely is a real
  optimization:** caching. Hash the raw query text, check a cache for
  that exact hash, skip re-embedding and re-searching entirely on a
  repeat (semantic/exact-match caching, e.g. GPTCache). Real speedup,
  just for repeated questions, not for making a novel one faster to
  understand.

## 7. "A synonym lookup that converts the sentence to plain/canonical text, then hash that"

Real, and actually shipped in production: Elasticsearch's text pipeline
has a built-in synonym filter that does exactly this ahead of indexing --
map words to a canonical form (a thesaurus/WordNet-style synset) before
hashing. Genuinely closes part of the gap from idea 5: two sentences using
different synonyms for the same concept now canonicalize to the same text
first. Where it still falls short of embeddings, for principled reasons:
- **No context (polysemy).** "Bank" (money) vs. "bank" (river) -- a fixed
  word-to-synonym table can't disambiguate without reading the sentence
  around it. This is the actual reason *contextual* embeddings
  (BERT-style) were a real breakthrough over older *static* word
  embeddings (word2vec/GloVe), which had this exact limitation.
- **No compositional meaning.** Word-by-word synonym substitution doesn't
  capture negation ("not good" vs. "good"), idiom, or meaning from word
  order.
- **Binary-ish matching, not graded similarity.** Embeddings give a
  continuous distance to rank imperfect candidates by; canonicalize-then-
  hash is closer to match-or-not.
- **A hand-built dictionary always lags real language.** A trained model
  absorbs new slang/jargon/analogies from data; a synonym table only
  knows what a human already wrote into it.

## 8. "Update the hash based on new inputs, so things route to the same section even before it exists"

Real technique: **consistent hashing** (the mechanism behind DynamoDB,
Cassandra, memcached sharding, CDN routing). Every possible key sits on a
fixed hash ring independent of which buckets/nodes currently exist -- so a
key really can "route to a section that doesn't exist yet": when a new
node is added at some point on the ring, it just starts claiming whatever
keys already hash into its range, remapping only the small slice between
it and its nearest neighbor, not everything.

One correction worth keeping: a hash function or a trained embedding model
is *already* deterministic and *already* generalizes to input it's never
seen, without needing to be "updated" first -- that part isn't the hard
problem. The two places "updating" is a real, nontrivial cost:
- **Resizing the number of buckets/partitions** -- what consistent
  hashing minimizes the pain of.
- **The meaning space outgrowing the embedding model itself** -- fixed
  only by retraining/upgrading the model, and that means re-embedding and
  re-indexing the *entire* existing corpus, since vectors from two model
  versions aren't comparable at all. A real, cited operational cost in
  production RAG systems.

---

**The through-line, if it's ever worth stating in one sentence:** every
idea in this log turned out to be a real, named, already-used technique --
the actual skill being exercised across all eight steps wasn't inventing
something new, it was correctly guessing that a clean intuition probably
already has a name, and then finding the one detail (avalanche effect,
lexical-vs-semantic, tokenize-before-hash, deterministic-by-construction)
that separates "close" from "exactly right."
