# Install the pinned R packages of the supporting GLMMs (#34) from a dated CRAN snapshot.
#
# Usage (CI job "r" in .github/workflows/analysis.yml, or an analyst machine with the
# pinned R version): Rscript --vanilla analysis/r/install.R
#
# Reads analysis/r/pins.dcf, refuses another R version, installs every pinned package and
# its dependencies from the Posit Package Manager snapshot of that date into R_LIBS_USER,
# then runs check_pins.R. Nothing is installed system-wide.

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

here <- script_dir()
pins <- read_pins(file.path(here, "pins.dcf"))
if (as.character(getRversion()) != pins[["R"]]) {
  stop(sprintf("R %s is pinned; this is R %s", pins[["R"]], getRversion()))
}
packages <- setdiff(names(pins), c("R", "Snapshot"))

codename <- ""
if (Sys.info()[["sysname"]] == "Linux" && file.exists("/etc/os-release")) {
  os <- grep("^VERSION_CODENAME=", readLines("/etc/os-release"), value = TRUE)
  if (length(os)) codename <- gsub("\"", "", sub("^VERSION_CODENAME=", "", os[[1]]))
}
repo <- if (nzchar(codename)) {
  sprintf("https://packagemanager.posit.co/cran/__linux__/%s/%s", codename, pins[["Snapshot"]])
} else {
  sprintf("https://packagemanager.posit.co/cran/%s", pins[["Snapshot"]])
}
# The user agent lets the package manager serve Linux binaries for this R version.
options(
  repos = c(CRAN = repo),
  HTTPUserAgent = sprintf(
    "R/%s R (%s)", getRversion(),
    paste(getRversion(), R.version[["platform"]], R.version[["arch"]], R.version[["os"]])
  )
)

lib <- path.expand(Sys.getenv("R_LIBS_USER"))
if (!nzchar(lib)) stop("R_LIBS_USER is not set")
dir.create(lib, recursive = TRUE, showWarnings = FALSE)
.libPaths(c(lib, .libPaths()))
cat(sprintf("Installing %s from %s into %s\n", paste(packages, collapse = ", "), repo, lib))
install.packages(packages, lib = lib, dependencies = c("Depends", "Imports", "LinkingTo"))

status <- system2(file.path(R.home("bin"), "Rscript"), c("--vanilla", file.path(here, "check_pins.R")))
quit(status = status)
