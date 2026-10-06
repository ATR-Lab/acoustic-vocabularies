# Fit one rung of a supporting GLMM (#34) with lme4::glmer and report its checks.
#
# Usage (called by av_analysis.rbridge.run_r, never by hand in an analysis):
#   Rscript --vanilla analysis/r/glmm.R <folder>/request.json
#
# The request names the model data CSV (in the same folder), the lme4 formula of the rung,
# the response column, factor columns with their levels (the first level is the
# reference), numeric columns, grouping columns, the optimizer and the singularity
# tolerance. The script writes <folder>/result.json with the versions of R and of every
# pinned package, the optimizer return code, convergence and singularity flags, every
# warning and message verbatim, and (when the fit ran) the fixed effects, the random-effect
# standard deviations and correlations, the number of observations, the log likelihood and
# the AIC. The decision whether a rung is accepted (converged and not singular) and the
# order of the fallback ladder belong to the Python side (av_analysis.glmm).

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) stop("usage: Rscript --vanilla glmm.R <request.json>")
request_path <- normalizePath(args[[1L]])
folder <- dirname(request_path)

suppressPackageStartupMessages({
  library(jsonlite)
  library(lme4)
})

req <- fromJSON(request_path, simplifyVector = FALSE)

pin_names <- function() {
  file <- sub("^--file=", "", grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE))
  lines <- trimws(readLines(file.path(dirname(normalizePath(file)), "pins.dcf"), warn = FALSE))
  lines <- lines[nzchar(lines) & !startsWith(lines, "#")]
  keys <- trimws(sub(":.*$", "", lines))
  setdiff(keys, c("R", "Snapshot"))
}

versions <- list(R = as.character(getRversion()))
for (p in pin_names()) {
  versions[[p]] <- tryCatch(utils::packageDescription(p)$Version, error = function(e) NA_character_)
}

data <- utils::read.csv(file.path(folder, req$data), colClasses = "character",
                        stringsAsFactors = FALSE, check.names = FALSE, na.strings = character(0))
response <- req$response
data[[response]] <- as.integer(data[[response]])
if (anyNA(data[[response]]) || any(!data[[response]] %in% c(0L, 1L))) {
  stop("response must be 0 or 1 on every row")
}
for (name in names(req$factors)) {
  levels <- unlist(req$factors[[name]])
  if (any(!data[[name]] %in% levels)) stop(sprintf("column %s has a value outside its levels", name))
  data[[name]] <- factor(data[[name]], levels = levels)
}
for (name in unlist(req$numeric)) data[[name]] <- as.numeric(data[[name]])
for (name in unlist(req$groups)) data[[name]] <- factor(data[[name]])

messages <- character(0)
fit_error <- NULL
fit <- withCallingHandlers(
  tryCatch(
    glmer(
      stats::as.formula(req$formula),
      data = data,
      family = stats::binomial(link = "logit"),
      control = glmerControl(optimizer = req$optimizer, optCtrl = list(maxfun = req$max_fun))
    ),
    error = function(e) {
      fit_error <<- conditionMessage(e)
      NULL
    }
  ),
  warning = function(w) {
    messages <<- c(messages, paste0("warning: ", conditionMessage(w)))
    invokeRestart("muffleWarning")
  },
  message = function(m) {
    messages <<- c(messages, paste0("message: ", trimws(conditionMessage(m))))
    invokeRestart("muffleMessage")
  }
)

result <- list(
  format = "av-analysis/glmm-result",
  model_id = req$model_id,
  formula = req$formula,
  versions = versions,
  optimizer = req$optimizer,
  error = fit_error,
  optimizer_code = NULL,
  converged = FALSE,
  singular = NULL,
  messages = as.list(messages),
  fixed = list(),
  random = list(),
  n_obs = NULL,
  loglik = NULL,
  aic = NULL
)

if (!is.null(fit)) {
  conv <- fit@optinfo$conv
  code <- if (is.null(conv$opt)) 0L else as.integer(conv$opt)
  check_msgs <- unlist(conv$lme4$messages)
  check_msgs <- check_msgs[!grepl("singular", check_msgs, ignore.case = TRUE)]
  warned <- any(grepl("converge", messages, ignore.case = TRUE))
  result$optimizer_code <- code
  result$converged <- (code == 0L) && length(check_msgs) == 0L && !warned
  if (length(check_msgs)) result$messages <- c(result$messages, as.list(paste0("check: ", check_msgs)))
  result$singular <- isSingular(fit, tol = req$singular_tol)
  cf <- stats::coef(summary(fit))
  result$fixed <- lapply(seq_len(nrow(cf)), function(i) list(
    term = rownames(cf)[[i]],
    estimate = unname(cf[i, 1L]),
    se = unname(cf[i, 2L]),
    z = unname(cf[i, 3L]),
    p = unname(cf[i, 4L])
  ))
  vc <- as.data.frame(VarCorr(fit))
  result$random <- lapply(seq_len(nrow(vc)), function(i) list(
    group = vc$grp[[i]],
    term1 = vc$var1[[i]],
    term2 = if (is.na(vc$var2[[i]])) NULL else vc$var2[[i]],
    sdcor = unname(vc$sdcor[[i]])
  ))
  result$n_obs <- stats::nobs(fit)
  result$loglik <- as.numeric(stats::logLik(fit))
  result$aic <- stats::AIC(fit)
}

write_json(result, file.path(folder, "result.json"), auto_unbox = TRUE, digits = NA,
           null = "null", na = "null", pretty = TRUE)
