"""Train DOXA logistic heads on the frozen dataset and export portable weights."""
from argparse import ArgumentParser
from collections import Counter
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from math import log
from time import perf_counter
import json
import platform

import numpy as np
import scipy
import sklearn
import yaml
from scipy.sparse import csr_matrix
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from threadpoolctl import threadpool_limits

from doxa.features import get_spec, grams, vector
from doxa.models import CLASS_ORDER, CRITERIA, RESEARCH_ONLY, PortableModel, input_error

SEED = 1729
CS = (0.1, 1., 10.)
MIN_SUPPORT = 6


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def hash_file(path):
    return sha256(path.read_bytes()).hexdigest()


def write_sums(folder):
    sha_path = folder / "SHA256SUMS"
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p != sha_path)
    sha_path.write_text(
        "".join(f"{hash_file(p)}  {p.relative_to(folder).as_posix()}\n" for p in files),
        encoding="utf-8",
    )


def read_table(dataset, table, split):
    path = dataset / "tables" / table / f"{split}.jsonl"
    if path.exists():
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def data_matrices(dataset):
    result = {}
    for split in ("train", "validation", "test", "challenge"):
        cases = {
            row["case_id"]: row for row in read_table(dataset, "cases", split) or []
        }
        result[split] = []
        for judgment in read_table(dataset, "judgments", split) or []:
            case = cases[judgment["case_id"]]
            context = "\n".join(segment["text"] for segment in case["context_segments"])
            if input_error(case["text"], context):
                raise ValueError(f"Invalid model input: {case['case_id']}")
            result[split].append({
                "case": case, "judgment": judgment, "input": (case["text"], context),
            })
    return result


def fit_features(training):
    inputs = sorted({row["input"] for row in training})
    df = Counter()
    for text, context in inputs:
        df.update((grams(text, "t") | grams(context, "c")).keys())
    keys = sorted(df, key=lambda key: (-df[key], key))[:get_spec()["max_vocabulary"]]
    vocab = {key: idx for idx, key in enumerate(keys)}
    idf = np.array(
        [log((1 + len(inputs)) / (1 + df[key])) + 1 for key in keys],
        dtype="<f4",
    )
    return vocab, idf, len(inputs)


def matrix(inputs: Iterable, vocab, idf):
    indices, data, indptr, known = [], [], [0], []
    for text, context in inputs:
        _, weights, text_known = vector(text, context, vocab, idf)
        indices.extend(weights)
        data.extend(weights.values())
        indptr.append(len(indices))
        known.append(text_known)
    return csr_matrix((data, indices, indptr), shape=(len(known), len(vocab))), known


def support(rows):
    return {
        label: len({
            row["case"]["family_id"] for row in rows
            if row["judgment"]["reference_decision"] == label
        }) for label in CLASS_ORDER
    }


def class_code(row):
    return CLASS_ORDER.index(row["judgment"]["reference_decision"])


def by_criterion(rows, criterion):
    return [i for i, row in enumerate(rows) if row["judgment"]["criterion_id"] == criterion]


def peak_rss_mib():
    try:
        import resource
    except ImportError:
        return None
    scale = 1024 ** (2 if platform.system() == "Darwin" else 1)
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale


def load_config(path, defaults, variable=()):
    if path is None:
        return defaults.copy()
    supplied = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(supplied, dict):
        raise ValueError("Configuration must be a mapping")
    unexpected = supplied.keys() - defaults.keys()
    if unexpected:
        raise ValueError(f"Unknown configuration keys: {sorted(unexpected)}")
    for key, value in supplied.items():
        if key not in variable and defaults[key] != value:
            raise ValueError(f"{key} must match the v0.1 portable contract: {defaults[key]}")
    return defaults | supplied


def train(dataset=Path("release_staging/dataset"), folder=Path("release_staging/model"), config=None):
    spec = get_spec()
    defaults = {key: spec[key] for key in (
        "feature_version", "ngram_lengths", "max_vocabulary",
        "max_text_codepoints", "max_context_codepoints",
    )}
    options = load_config(config, defaults | {
        "regularization_candidates": list(CS), "seed": SEED,
        "minimum_training_families_per_class": MIN_SUPPORT,
        "abstention": "no_known_passage_features",
    }, variable=("regularization_candidates", "seed"))
    c_values = options["regularization_candidates"]
    if not isinstance(c_values, list) or not c_values or any(
        not isinstance(c, (int, float)) or not np.isfinite(c) or c <= 0 for c in c_values
    ):
        raise ValueError("Regularization candidates must be a list of finite positive values")
    seed = options["seed"]
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("seed must be an unsigned 32-bit integer")
    start = perf_counter()
    dataset, folder = Path(dataset), Path(folder)
    rows = data_matrices(dataset)
    if not rows["train"]:
        raise ValueError("No training judgments in staged dataset")
    folder.mkdir(parents=True, exist_ok=True)
    vocab, idf, n_inputs = fit_features(rows["train"])
    train_matrix, _ = matrix((row["input"] for row in rows["train"]), vocab, idf)
    valid_matrix, valid_known = matrix((row["input"] for row in rows["validation"]), vocab, idf)
    trained, coefficients, intercepts, records = [], [], [], {}
    with threadpool_limits(limits=1):
        for criterion in CRITERIA:
            train_idx = sorted(
                by_criterion(rows["train"], criterion),
                key=lambda i: rows["train"][i]["judgment"]["judgment_id"],
            )
            valid_idx = by_criterion(rows["validation"], criterion)
            train_support = support([rows["train"][i] for i in train_idx])
            y = [class_code(rows["train"][i]) for i in train_idx]
            prior = Counter(y)
            most_frequent = min(prior, key=lambda k: (-prior[k], k)) if prior else None
            status = {
                "family_counts": train_support,
                "class_counts": {label: prior[i] for i, label in enumerate(CLASS_ORDER)},
                "validation_rows": len(valid_idx),
                "most_frequent": CLASS_ORDER[most_frequent] if most_frequent is not None else None,
            }
            records[criterion] = status
            if criterion in RESEARCH_ONLY or min(train_support.values()) < MIN_SUPPORT:
                status.update(
                    status="unsupported_criterion",
                    reason="requires_reference_or_comparator" if criterion in RESEARCH_ONLY
                    else "insufficient_training_families",
                )
                continue
            candidates = {}
            y_valid = [class_code(rows["validation"][i]) for i in valid_idx]
            for c in c_values if valid_idx else (1.,):
                fit = LogisticRegression(C=c, solver="lbfgs", max_iter=1500, random_state=seed)
                fit.fit(train_matrix[train_idx], y)
                predicted = fit.predict(valid_matrix[valid_idx]) if valid_idx else []
                scored = [
                    int(label) if valid_known[idx] else -1
                    for idx, label in zip(valid_idx, predicted, strict=True)
                ]
                score = f1_score(y_valid, scored, labels=[0, 1, 2], average="macro",
                                 zero_division=0) if valid_idx else None
                candidates[c] = (score, fit)
            best_c = max(candidates, key=lambda c: (candidates[c][0] or 0, -c))
            best = candidates[best_c][1]
            status.update(
                status="supported", selected_c=best_c,
                c_selection="validation_macro_f1" if valid_idx else "untuned_default",
                validation_macro_f1={str(c): score for c, (score, _) in candidates.items()},
            )
            trained.append(criterion)
            coefficients.append(best.coef_)
            intercepts.append(best.intercept_)

    dataset_hashes = {
        p.relative_to(dataset).as_posix(): hash_file(p)
        for p in sorted((dataset / "tables").rglob("*.jsonl"))
    }
    dataset_hash = sha256(json.dumps(dataset_hashes, sort_keys=True).encode()).hexdigest()
    coeff_path = folder / "coefficients.f32"
    intercept_path = folder / "intercepts.f32"
    idf.tofile(folder / "idf.f32")
    np.asarray(coefficients, dtype="<f4").tofile(coeff_path)
    np.asarray(intercepts, dtype="<f4").tofile(intercept_path)
    write_json(folder / "vocabulary.json", vocab)
    model_hash = sha256(b"".join(
        (folder / name).read_bytes()
        for name in ("vocabulary.json", "idf.f32", "coefficients.f32", "intercepts.f32")
    )).hexdigest()
    manifest = {
        "architecture": "multinomial per-criterion logistic regression",
        "class_order": list(CLASS_ORDER), "heads": trained, "criteria": list(CRITERIA),
        "shapes": {"idf": [len(vocab)], "coefficients": [len(trained), 3, len(vocab)],
                   "intercepts": [len(trained), 3]},
        "dtype": "little-endian float32", "array_order": "row-major",
        "model_revision": "local-sha256:" + model_hash,
        "training_data_revision": "local-sha256:" + dataset_hash,
        "training_data_sha256": dataset_hash, "training_data_files": dataset_hashes,
        "model_sha256": model_hash, "review_policy_version": "doxa-review-v0.1",
    }
    write_json(folder / "model_manifest.json", manifest)
    write_json(folder / "feature_spec.json", get_spec() | {"N": n_inputs})
    write_json(folder / "training_config.json", {
        "seed": seed, "regularization_candidates": c_values, "support_floor": MIN_SUPPORT,
        "abstention": "invalid input or zero known passage features; no score threshold",
        "training_seconds": round(perf_counter() - start, 4),
        "peak_rss_mib": peak_rss_mib(), "platform": platform.platform(), "python": platform.python_version(),
        "numpy": np.__version__, "scipy": scipy.__version__, "sklearn": sklearn.__version__,
        "source_files": {
            path.relative_to(Path.cwd()).as_posix(): hash_file(path)
            for path in sorted((Path.cwd() / "src" / "doxa").rglob("*.py"))
            if any(part in path.parts for part in ("features", "models", "evaluation"))
        },
    })
    write_json(folder / "training_support.json", records)
    model = PortableModel(folder)
    fixtures = [
        ("The cabinet should reverse its ruling.", ""),
        ("“Café” Cafe\u0301 😀", "A quote.\r\nThe author rebuts it."),
        ("AA\tBB\u202fCC", "\nreported\u00a0by"),
        ("🦕🦕🦕🦕🦕", ""), ("🦕🦕🦕🦕🦕", rows["train"][0]["input"][1]),
        ("\t\r\n", ""), ("x" * 6001, ""),
    ] + [row["input"] for row in rows["train"][:8]]
    with (folder / "parity_cases.jsonl").open("w", encoding="utf-8") as out:
        for text, context in fixtures:
            counts, _, _ = ({}, {}, False) if input_error(text, context) else vector(
                text, context, vocab, idf
            )
            out.write(json.dumps({
                "text": text, "context": context, "counts": sorted(counts.items()),
                "output": model.predict(text, context),
            }, ensure_ascii=False) + "\n")
    write_sums(folder)
    print(f"Trained {len(trained)} heads on {len(rows['train'])} rows, {len(vocab)} features.")


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("release_staging/dataset"))
    parser.add_argument("--model", type=Path, default=Path("release_staging/model"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--evaluate", action="store_true")
    args = parser.parse_args()
    if args.evaluate:
        from doxa.evaluation import evaluate
        evaluate(args.dataset, args.model, args.config)
    else:
        train(args.dataset, args.model, args.config)


if __name__ == "__main__":
    main()
