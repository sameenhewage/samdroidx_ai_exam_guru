import type { ReviewLanguage } from "@/lib/review-language";

const english = {
  title: "Review curriculum mapping",
  back: "Back to material",
  intro:
    "Choose one content section. Check its original page and accepted teaching points before confirming where it belongs in the curriculum.",
  sections: "Content sections",
  page: "Page",
  section: "Section",
  previous: "Previous 20 sections",
  next: "Next 20 sections",
  refreshList: "Refresh sections",
  loading: "Loading curriculum review…",
  empty: "No content sections are available yet.",
  emptyHelp:
    "Finish the source-page and material-detail checks first. Checked content is prepared automatically; return here to review each available section.",
  reviewSource: "Review page content",
  selectSection:
    "Select a section to compare its source and curriculum mapping.",
  readonly: "Reviewer access is read-only.",
  mapping: "Curriculum mapping",
  saved: "Curriculum mapping saved.",
  confirmed: "Mapping confirmed",
  notConfirmed: "Mapping not yet confirmed",
  separate:
    "Source checking, curriculum mapping and AI preparation are separate checks. Saving a mapping does not by itself make this section ready for AI.",
  sourceChanged:
    "The source or its curriculum details need review before saving.",
  locked:
    "Already assigned in the source. This review cannot replace or widen it.",
  optional:
    "Unit, lesson and the more specific teaching labels are optional when not already assigned in the source.",
  module: "Unit / module",
  lesson: "Lesson",
  competency: "Teaching area",
  skill: "Skill",
  subSkill: "Sub-skill",
  concept: "Concept",
  none: "Not specified",
  chooseArea: "Choose a teaching area",
  unavailableChoice: "Previous choice is no longer available. Choose again.",
  unavailableLabel: "Source assignment label unavailable",
  lessonLinks: "Lesson teaching links:",
  explicit:
    "These are curriculum guidance only. Choose and confirm the mapping yourself.",
  reason: "Reason for this mapping",
  consent: "I confirm this curriculum mapping for this section.",
  save: "Save curriculum mapping",
  saving: "Saving…",
  refresh: "Reload latest version",
  rebase: "Keep draft with latest version",
  discard: "Discard draft",
  oneDraft: "Finish or discard this draft before starting a different action.",
  rebaseHelp:
    "Compare the latest source and available curriculum choices, then keep your draft with that version. You must confirm again before saving.",
  leave:
    "Discard this unsaved curriculum review and leave? Cancel keeps your draft.",
  ai: "AI preparation",
  not_requested: "Curriculum mapping needed",
  waiting_configuration: "AI preparation needs setup",
  pending: "Waiting for AI preparation",
  queued: "Preparing for AI…",
  ready: "Ready for AI",
  needs_attention: "AI preparation needs attention",
  superseded: "This section has changed",
  not_searchable: "Not available for question generation",
  configuration_changed: "AI preparation settings changed",
  inconsistent: "AI readiness could not be confirmed",
  setupHelp:
    "The mapping has been saved. Ask an administrator to complete AI preparation setup; do not repeat the curriculum review.",
  attentionHelp:
    "The saved mapping is kept. Check the latest status before trying AI preparation again.",
  noSearchHelp:
    "The source is preserved, but this section has no content available for question generation.",
  retry: "Try AI preparation again",
  retryReason: "Reason for trying again",
  retryConsent: "I want to try AI preparation again for this section.",
  retried:
    "AI preparation requested. The curriculum review has not been repeated.",
  expired:
    "Your session has expired. Sign in again before continuing. Your draft has been kept.",
  denied:
    "Your account does not have permission for this action. Your draft has been kept.",
  missing:
    "This material or section is no longer available. Your draft has been kept.",
  changed:
    "This section or its settings changed. Your draft has been kept. Reload the latest version before trying again.",
  invalid:
    "The review could not be accepted. Check the selected curriculum choices and reason. Your draft has been kept; reload the latest version before trying again.",
  unavailable:
    "The review service is unavailable. Your draft has been kept. Reload the latest version before trying again.",
  failed:
    "The latest version could not be loaded. Your draft has been kept. Reload before trying again.",
};

const sinhala: Record<keyof typeof english, string> = {
  title: "විෂයමාලා ගැළපීම පරීක්ෂා කරන්න",
  back: "මූලාශ්‍රය වෙත ආපසු",
  intro:
    "එක් අන්තර්ගත කොටසක් තෝරන්න. එහි විෂයමාලා ගැළපීම තහවුරු කිරීමට පෙර මුල් පිටුව සහ පිළිගත් ඉගැන්වීමේ කරුණු පරීක්ෂා කරන්න.",
  sections: "අන්තර්ගත කොටස්",
  page: "පිටුව",
  section: "කොටස",
  previous: "පෙර කොටස් 20",
  next: "ඊළඟ කොටස් 20",
  refreshList: "කොටස් නැවත බලන්න",
  loading: "විෂයමාලා පරීක්ෂාව පූරණය වෙමින් පවතී…",
  empty: "අන්තර්ගත කොටස් තවම ලබා ගත නොහැක.",
  emptyHelp:
    "පළමුව මූලාශ්‍රයේ පිටු සහ තොරතුරු පරීක්ෂා කර අවසන් කරන්න. පරීක්ෂා කළ අන්තර්ගතය ස්වයංක්‍රීයව සකස් වේ. ඉන් පසු එක් එක් කොටස මෙහි පරීක්ෂා කරන්න.",
  reviewSource: "පිටුවේ අන්තර්ගතය පරීක්ෂා කරන්න",
  selectSection: "මුල් පිටුව සහ විෂයමාලා ගැළපීම සැසඳීමට කොටසක් තෝරන්න.",
  readonly: "ඔබට බැලිය හැකි නමුත් වෙනස් කළ නොහැක.",
  mapping: "විෂයමාලා ගැළපීම",
  saved: "විෂයමාලා ගැළපීම සුරකින ලදී.",
  confirmed: "ගැළපීම තහවුරු කර ඇත",
  notConfirmed: "ගැළපීම තවම තහවුරු කර නැත",
  separate:
    "මූලාශ්‍රය පරීක්ෂා කිරීම, විෂයමාලා ගැළපීම සහ AI සඳහා සකස් කිරීම වෙන වෙනම පරීක්ෂා වේ. ගැළපීම සුරැකීමෙන් පමණක් මෙම කොටස AI සඳහා සූදානම් නොවේ.",
  sourceChanged:
    "සුරැකීමට පෙර මූලාශ්‍රය හෝ එහි විෂයමාලා තොරතුරු නැවත පරීක්ෂා කළ යුතුය.",
  locked:
    "මූලාශ්‍රයට දැනටමත් වෙන් කර ඇත. මෙම පරීක්ෂාවෙන් එය වෙනස් හෝ පුළුල් කළ නොහැක.",
  optional:
    "මූලාශ්‍රයේ දැනටමත් වෙන් කර නැත්නම් ඒකකය, පාඩම සහ වඩා නිශ්චිත ඉගැන්වීමේ ලේබල තෝරා ගැනීම අනිවාර්ය නොවේ.",
  module: "ඒකකය / මොඩියුලය",
  lesson: "පාඩම",
  competency: "ඉගැන්වීමේ ක්ෂේත්‍රය",
  skill: "කුසලතාව",
  subSkill: "උප කුසලතාව",
  concept: "සංකල්පය",
  none: "සඳහන් කර නැත",
  chooseArea: "ඉගැන්වීමේ ක්ෂේත්‍රයක් තෝරන්න",
  unavailableChoice: "පෙර තේරීම දැන් ලබා ගත නොහැක. නැවත තෝරන්න.",
  unavailableLabel: "මූලාශ්‍රයේ වෙන් කළ ලේබලය ලබා ගත නොහැක",
  lessonLinks: "පාඩමේ ඉගැන්වීමේ සබැඳි:",
  explicit: "මේවා විෂයමාලා මඟපෙන්වීම පමණි. ගැළපීම ඔබම තෝරා තහවුරු කරන්න.",
  reason: "මෙම ගැළපීමට හේතුව",
  consent: "මෙම කොටසේ විෂයමාලා ගැළපීම මම තහවුරු කරමි.",
  save: "විෂයමාලා ගැළපීම සුරකින්න",
  saving: "සුරකිමින් පවතී…",
  refresh: "නවතම අනුවාදය පූරණය කරන්න",
  rebase: "නවතම අනුවාදය සමඟ කෙටුම්පත රඳවා ගන්න",
  discard: "කෙටුම්පත අත්හරින්න",
  oneDraft:
    "වෙනත් ක්‍රියාවක් ආරම්භ කිරීමට පෙර මෙම කෙටුම්පත සම්පූර්ණ කරන්න හෝ අත්හරින්න.",
  rebaseHelp:
    "නවතම මූලාශ්‍රය සහ ලබා ගත හැකි විෂයමාලා තේරීම් සසඳා එම අනුවාදය සමඟ කෙටුම්පත රඳවා ගන්න. සුරැකීමට පෙර නැවත තහවුරු කළ යුතුය.",
  leave:
    "සුරැකී නැති මෙම විෂයමාලා පරීක්ෂාව අත්හැර යන්නද? අවලංගු කිරීමෙන් කෙටුම්පත රඳවා ගනී.",
  ai: "AI සඳහා සකස් කිරීම",
  not_requested: "විෂයමාලා ගැළපීම අවශ්‍යයි",
  waiting_configuration: "AI සඳහා සකස් කිරීමට සැකසුම් අවශ්‍යයි",
  pending: "AI සඳහා සකස් කිරීමට රැඳී සිටී",
  queued: "AI සඳහා සකස් කරමින් පවතී…",
  ready: "AI සඳහා සූදානම්",
  needs_attention: "AI සඳහා සකස් කිරීමේ ගැටලුවක් පවතී",
  superseded: "මෙම කොටස වෙනස් වී ඇත",
  not_searchable: "ප්‍රශ්න සෑදීමට භාවිත කළ නොහැක",
  configuration_changed: "AI සඳහා සකස් කිරීමේ සැකසුම් වෙනස් වී ඇත",
  inconsistent: "AI සඳහා සූදානම තහවුරු කළ නොහැකි විය",
  setupHelp:
    "ගැළපීම සුරැකී ඇත. AI සඳහා සකස් කිරීමේ සැකසුම් සම්පූර්ණ කිරීමට පරිපාලකවරයෙකුගෙන් විමසන්න. විෂයමාලා පරීක්ෂාව නැවත කිරීම අවශ්‍ය නොවේ.",
  attentionHelp:
    "සුරැකි ගැළපීම රඳවා ඇත. AI සඳහා සකස් කිරීමට නැවත උත්සාහ කිරීමට පෙර නවතම තත්ත්වය බලන්න.",
  noSearchHelp:
    "මූලාශ්‍රය රඳවා ඇතත් මෙම කොටසෙන් ප්‍රශ්න සෑදීමට භාවිත කළ හැකි අන්තර්ගතයක් නොමැත.",
  retry: "AI සඳහා සකස් කිරීමට නැවත උත්සාහ කරන්න",
  retryReason: "නැවත උත්සාහ කිරීමට හේතුව",
  retryConsent: "මෙම කොටස AI සඳහා සකස් කිරීමට නැවත උත්සාහ කිරීමට මට අවශ්‍යයි.",
  retried: "AI සඳහා සකස් කිරීමට ඉල්ලා ඇත. විෂයමාලා පරීක්ෂාව නැවත කර නැත.",
  expired:
    "ඔබේ සැසිය අවසන් වී ඇත. ඉදිරියට යාමට නැවත පිවිසෙන්න. කෙටුම්පත රඳවා ඇත.",
  denied: "මෙම ක්‍රියාවට ඔබට අවසර නැත. කෙටුම්පත රඳවා ඇත.",
  missing: "මෙම මූලාශ්‍රය හෝ කොටස දැන් ලබා ගත නොහැක. කෙටුම්පත රඳවා ඇත.",
  changed:
    "මෙම කොටස හෝ එහි සැකසුම් වෙනස් වී ඇත. කෙටුම්පත රඳවා ඇත. නැවත උත්සාහ කිරීමට පෙර නවතම අනුවාදය පූරණය කරන්න.",
  invalid:
    "පරීක්ෂාව පිළිගත නොහැකි විය. විෂයමාලා තේරීම් සහ හේතුව පරීක්ෂා කරන්න. කෙටුම්පත රඳවා ඇත. නැවත උත්සාහ කිරීමට පෙර නවතම අනුවාදය පූරණය කරන්න.",
  unavailable:
    "පරීක්ෂා කිරීමේ සේවාව ලබා ගත නොහැක. කෙටුම්පත රඳවා ඇත. නැවත උත්සාහ කිරීමට පෙර නවතම අනුවාදය පූරණය කරන්න.",
  failed:
    "නවතම අනුවාදය පූරණය කළ නොහැකි විය. කෙටුම්පත රඳවා ඇත. නැවත උත්සාහ කිරීමට පෙර පූරණය කරන්න.",
};

export function curriculumReviewCopy(language: ReviewLanguage) {
  return language === "si" ? sinhala : english;
}
export type CurriculumReviewCopy = typeof english;
