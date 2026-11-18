using System.IO;
using System.Linq;
using AcousticVocab.Assessment;
using NUnit.Framework;

namespace AcousticVocab.DataLogging.Tests
{
    // Requirement trace: ownership ratings travel in the forms table, never in the
    // trial (accuracy) or exposure tables. Synthetic journal only.
    public sealed class RequirementTraceFormsExportTests
    {
        static readonly string Schedule = new string('b', 64), Epoch = new string('c', 32);

        [Test, Category("REQ_AN_12"), NUnit.Framework.Property("Requirement", "REQ-AN-12")]
        public void OwnershipRatingsExportOnlyInTheFormsTableSeparateFromAccuracyRows()
        {
            string raw = SyntheticData.Folder("trace-ownership"), output = SyntheticData.Folder("trace-ownership-export");
            using (var journal = new DataJournal(raw, SyntheticData.Identity, new string('3', 32), () => 100))
            {
                SyntheticData.Trial(journal, "protected-before-forms");
                var forms = new AssessmentDataJournal(journal, Schedule);
                forms.Append(new AssessmentRecord("forms_started", Schedule, 1, "forms", clockEpoch: Epoch));
                forms.Append(new AssessmentRecord("rating", Schedule, 2, "forms", "ownership", 6, clockEpoch: Epoch));
            }
            var tables = DataDeriver.Derive(DataJournal.Verify(raw, SyntheticData.Identity), SyntheticData.Identity);
            Assert.That(tables.Trials.Count, Is.EqualTo(1), "Forms add no trial rows"); Assert.That(tables.Exposures.Count, Is.EqualTo(1));
            var bundle = ExportBundle.Create(raw, output, SyntheticData.Identity, ExportHeaders.Provisional());
            var stageRows = File.ReadAllLines(Path.Combine(output, AssessmentExport.Table));
            var ownership = stageRows.Skip(1).Where(x => x.Contains(",rating,forms,ownership,6,")).ToArray();
            Assert.That(ownership.Length, Is.EqualTo(1));
            foreach (string table in new[] { "trial-log.csv", "exposure-ledger.csv" })
                Assert.That(File.ReadAllText(Path.Combine(output, table)), Does.Not.Contain("ownership").And.Not.Contain("forms_started"), table);
            Assert.That(bundle.Files.Select(x => x.RelativePath), Does.Contain(AssessmentExport.Table));
        }
    }
}
