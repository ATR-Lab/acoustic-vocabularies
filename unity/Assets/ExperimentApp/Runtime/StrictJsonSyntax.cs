using System;
using System.Text.RegularExpressions;

namespace AcousticVocab.Foundation
{
    // Json.NET accepts several JavaScript extensions. Reject them before deserialization.
    internal sealed class StrictJsonSyntax
    {
        readonly string text;
        int at;
        StrictJsonSyntax(string text) { this.text = text; }
        public static void Validate(string text)
        {
            var parser = new StrictJsonSyntax(text);
            parser.Value(0); parser.Space();
            if (parser.at != text.Length) throw new ConfigurationFault("json_syntax");
        }
        void Space() { while (at < text.Length && " \r\n\t".IndexOf(text[at]) >= 0) at++; }
        bool Take(char c) { Space(); if (at < text.Length && text[at] == c) { at++; return true; } return false; }
        void Need(char c) { if (!Take(c)) throw new ConfigurationFault("json_syntax"); }
        void Value(int depth)
        {
            if (depth > 16) throw new ConfigurationFault("json_depth");
            Space(); if (at >= text.Length) throw new ConfigurationFault("json_syntax");
            if (Take('{'))
            {
                if (Take('}')) return;
                do { String(); Need(':'); Value(depth + 1); } while (Take(','));
                Need('}'); return;
            }
            if (Take('['))
            {
                if (Take(']')) return;
                do { Value(depth + 1); } while (Take(','));
                Need(']'); return;
            }
            if (text[at] == '"') { String(); return; }
            int start = at;
            while (at < text.Length && " \t\r\n,]}".IndexOf(text[at]) < 0) at++;
            string token = text.Substring(start, at - start);
            if (token != "true" && token != "false" && token != "null" && !Regex.IsMatch(token, "^-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?$"))
                throw new ConfigurationFault("json_syntax");
        }
        void String()
        {
            Need('"');
            while (at < text.Length)
            {
                char c = text[at++]; if (c == '"') return;
                if (c < 32) throw new ConfigurationFault("json_syntax");
                if (c != '\\') continue;
                if (at == text.Length) throw new ConfigurationFault("json_syntax");
                char escaped = text[at++];
                if (escaped == 'u')
                {
                    for (int i = 0; i < 4; i++)
                        if (at == text.Length || !Uri.IsHexDigit(text[at++])) throw new ConfigurationFault("json_syntax");
                }
                else if ("\"\\/bfnrt".IndexOf(escaped) < 0) throw new ConfigurationFault("json_syntax");
            }
            throw new ConfigurationFault("json_syntax");
        }
    }
}
