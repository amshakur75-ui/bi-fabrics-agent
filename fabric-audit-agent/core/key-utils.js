/** Extract the domain prefix from a finding key ("type::resource" -> "type" -> "domain"). */
export function domainOf(key) {
  if (typeof key !== 'string') return 'other';
  const type = key.split('::')[0];
  return type.includes('.') ? type.split('.')[0] : 'other';
}
