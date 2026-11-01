// Law Office Fee Typical ($) values from the complete 48-row LOMK Business
// Opportunity Intelligence Master. Values marked N/A in the master return null.
// More specific services are intentionally evaluated before broad form numbers.
const FEE_RULES = [
  [/h-?1b.*(?:new|cap|lottery|specialty occupation)|(?:new|cap).*h-?1b/i, 3200],
  [/h-?1b.*(?:extension|transfer|portability|new employer)|(?:extension|transfer).*h-?1b/i, 2000],
  [/h-?1b.*(?:amendment|material change|new location|job duties)|(?:amendment|material change).*h-?1b/i, 1500],
  [/\bperm\b|labor certification|eta-?9089/i, 4000], [/eb-?5|immigrant investor|i-?526e|regional center/i, 12000],
  [/eb-?1a|extraordinary ability|self.?petition.*o-?1/i, 4500], [/eb-?1b|outstanding professor|outstanding researcher/i, 3800],
  [/eb-?1c|multinational manager|manager.*executive.*green card/i, 4200], [/national interest waiver|\bniw\b/i, 3800],
  [/eb-?2|advanced degree/i, 3800], [/eb-?3.*(?:skilled|professional)|(?:skilled worker|bachelors degree).*perm/i, 3200],
  [/eb-?4|special immigrant|religious worker|\bi-?360\b/i, 4000], [/\bi-?140\b|immigrant worker petition|priority date/i, 1500],
  [/employment.*(?:adjustment of status|aos)|(?:i-?485).*employment/i, 1600],
  [/i-?130.*(?:i-?485|aos).*(?:spouse|marriage)|(?:marriage|spouse).*(?:green card|aos)/i, 3200],
  [/i-?130.*(?:i-?485|aos).*(?:child|parent)|family green card.*(?:child|parent)/i, 3000],
  [/\bk-?1\b|fianc(?:e|é)|i-?129f.*engagement/i, 2500], [/\bk-?3\b|spouse visa.*pending/i, 2500],
  [/i-?130.*(?:sibling|f4)|petition.*alien relative.*(?:spouse|child|sibling|parent)/i, 1200],
  [/\bi-?824\b/i, 400], [/\bb-?2\b.*(?:consular|ds-?160|visitor|tourist)|(?:visitor|tourist) visa.*(?:embassy|interview)/i, null],
  [/stem opt|i-?983/i, 750], [/\bopt\b|optional practical training|post.?completion/i, 750], [/\bf-?1\b.*(?:student|i-?20|sevis)|student status/i, 750],
  [/\bl-?1a\b|intracompany.*(?:manager|executive)/i, 3000], [/\bl-?1b\b|specialized knowledge/i, 2500],
  [/change of status.*(?:f-?1|student)|(?:b-?2|h-?4).*to.*f-?1/i, 750], [/change of status.*\bb-?2\b|(?:h-?1b|h-?4).*to.*b-?2/i, 750],
  [/change of status.*\bl-?2\b|l-?1 spouse.*status/i, 750], [/change of status.*\bh-?4\b|h-?1b spouse.*status/i, 750],
  [/h-?4.*(?:ead|employment authorization)|h4-?ead/i, 450],
  [/extension of current status|i-?539 extension|(?:h-?4|b-?2|f-?2) extension/i, 750], [/f-?1.*(?:premium|i-?907)|premium.*student status/i, 750],
  [/consular processing|\bnvc\b|ds-?260|immigrant visa abroad/i, 2000], [/\bi-?751\b|remove conditions|conditional residence/i, 2600],
  [/\bi-?864\b|affidavit of support|financial sponsor/i, 500], [/\bn-?400\b|naturalization|citizenship.*(?:civics|interview)/i, 800],
  [/\bi-?90\b|replace permanent resident|green card renewal/i, 600],
  [/ap.*ead renewal|combo card|(?:i-?131).*i-?765.*renewal/i, 500], [/advance parole.*renewal|travel document.*i-?131/i, 500],
  [/re.?entry permit|lpr travel|extended absence/i, 500], [/gc ead renewal|work permit.*(?:renewal|pending i-?485)|\bi-?765\b/i, 500],
];

export function mapTypicalLawOfficeFee(item) {
  const raw = item?.raw || item?.analysis || item?.record || {};
  const text = [raw.service_line, raw.category, raw.sub_category, raw.subcategory, raw.filing_type, raw.fee_source, item?.caseType, raw.fd_a_case_type, raw.case_type, item?.transcript, raw.transcript].filter(Boolean).join(" | ");
  const match = FEE_RULES.find(([pattern]) => pattern.test(text));
  return match ? match[1] : null;
}
