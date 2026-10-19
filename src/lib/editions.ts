const modules = import.meta.glob('../../content/editions/*.json', { eager: true, import: 'default' });
export type Edition = {date:string;status?:string;intro:string;weather?:{title:string;summary:string;high:number|null;low:number|null};stories:{section:string;title:string;summary:string;url?:string|null;tag?:string}[];markets?:{name:string;value:string;change?:string}[];events?:{date:string;title:string;description:string;url?:string|null}[];watchers?:{name:string;status:string}[];meetups?:{date:string;title:string;description:string;url:string}[]};
export const editions = Object.values(modules) as Edition[];
editions.sort((a,b)=>b.date.localeCompare(a.date));
export const latest = editions.find(e => e.status === 'published') ?? editions[0];
export function formatDate(date:string){return new Date(date+'T12:00:00').toLocaleDateString('en-CA',{weekday:'long',month:'long',day:'numeric',year:'numeric',timeZone:'America/Vancouver'});}
