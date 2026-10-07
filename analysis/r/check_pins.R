# Verify that R and every package pinned in analysis/r/pins.dcf have the pinned versions.
#
# Usage: Rscript --vanilla analysis/r/check_pins.R
# Prints one line per pin ("ok" or "MISMATCH") and exits 1 on any mismatch. The analysis
# tests marked needs_r run it, and the R bridge (#34) records the same versions in every
# GLMM log.

script_dir <- function() {
  args <- commandArgs(trailingOnly = FALSE)
  file <- sub("^--file=", "", args[grep("^--file=", args)])
  dirname(normalizePath(file))
}

read_pins <- function(path) {
  lines <- trimws(readLines(path, warn = FALSE))
  lines <- lines[nzchar(lines) & !startsWith(lines, "#")]
  stats::setNames(trimws(sub("^[^:]*:", "", lines)), trimws(sub(":.*$", "", lines)))
}

pins <- read_pins(file.path(script_dir(), "pins.dcf"))
ok <- TRUE
check <- function(name, pinned, installed) {
  same <- !is.na(installed) &&
    package_version(pinned, strict = FALSE) == package_version(installed, strict = FALSE)
  cat(sprintf("%-10s pinned %-10s installed %-10s %s\n", name, pinned,
              ifelse(is.na(installed), "missing", installed), if (same) "ok" else "MISMATCH"))
  if (!same) ok <<- FALSE
}

check("R", pins[["R"]], as.character(getRversion()))
for (p in setdiff(names(pins), c("R", "Snapshot"))) {
  installed <- tryCatch(as.character(utils::packageVersion(p)), error = function(e) NA_character_)
  check(p, pins[[p]], installed)
}
quit(status = if (ok) 0L else 1L)
