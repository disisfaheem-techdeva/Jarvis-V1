export const THEME_KEY = "td-theme";

/** Applies the saved theme before first paint. Dark is the default. */
export const themeScript = `try{if(localStorage.getItem("${THEME_KEY}")==="light")document.documentElement.dataset.theme="light"}catch(e){}`;
