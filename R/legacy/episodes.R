library(tidyverse)
library(here)

# IDEAS INTERESANTES PARA SEGUIR 
# - Desarrollar análisis de redes(cambios de estado, cambios de carba)
# - Carga por microorganismo causal
# - Dinámicas 1 carbapenemasa vs varias.

# Hay que pensar en una categorización de episodios: Muestras seguidas hasta gap de N dias

# Todo esto por episodio de paciente y con el resultado de la PCR nuestra.
# Calcular tiempo de positividad continua  por carbapenemasa
# Calcular tiempo hasta positivización (si empieza por negativo)


# Pasar a readers
df_episodes <- openxlsx::read.xlsx('data/episodios.xlsx')


ggplot(df_episodes, aes(x=n_samples_episode)) + geom_histogram()
df_episodes %>% 
  select(nhc, episode_id) %>% 
  distinct() %>% 
  ggplot(aes(x=episode_id))+ geom_histogram(binwidth = 1)


df_episodes %>% 
  filter(episode_day > 30) %>% 
  View(.)

df_episodes %>% 
  filter(tipo_episodio == "Episodio Positivo", n_samples_episode >1) %>% 
  arrange(nhc, entry_date) %>% 
  group_by(patient_episode) %>% 
  ggplot(aes(x = episode_day, y = carba_encoded_fis, group = patient_episode, color=nhc)) +
  geom_line(alpha = 0.5) + theme(legend.position = "none") + facet_wrap(~n_samples_cat, scale = "free")


df_episodes %>% 
  filter(tipo_episodio == "Episodio Positivo", n_samples_episode >15) %>% 
  arrange(nhc, entry_date) %>% 
  group_by(patient_episode) %>% 
  ggplot(aes(x = episode_day, y = carba_encoded_fis, group = patient_episode, color=nhc)) +
  geom_line(alpha = 0.5) + theme(legend.position = "none")


df_episodes %>% 
  filter(tipo_episodio == "Episodio Positivo", n_samples_episode >15) %>% 
  arrange(nhc, entry_date) %>% 
  group_by(patient_episode) %>% 
  ggplot(aes(x = episode_day, y = carba_encoded_fis, group = patient_episode, color=nhc)) +
  geom_line(alpha = 0.8) #+ theme(legend.position = "none")


# Análisis por carbapenemasa

oxa_long_episodes <- df_episodes %>% 
  filter(n_samples_episode > 15, tipo_episodio == "Episodio Positivo") %>% 
  group_by(nhc, patient_episode) %>% 
  filter(any(OXA_fis == 1, na.rm = TRUE)) %>% 
  arrange(nhc, entry_date) %>%
  group_by(patient_episode) %>% 
  ggplot(aes(x = episode_day, y = OXA_fis, group = patient_episode, color=nhc)) +
  geom_line(alpha = 0.8, linewidth = 1.1)  + 
  theme_bw() + theme(legend.position = "none") + facet_wrap(~n_samples_cat, scale = "free") + 
  labs(title = "Episodios OXA",
       x = "Día de episodio") 

oxa_long_episodes <- df_episodes %>% 
  filter(n_samples_episode > 15, tipo_episodio == "Episodio Positivo") %>% 
  group_by(nhc, patient_episode) %>% 
  filter(any(OXA_fis == 1, na.rm = TRUE)) %>% 
  arrange(nhc, entry_date) %>%
  group_by(patient_episode) %>% 
  ggplot(aes(x = episode_day, y = OXA_fis, group = patient_episode, color = nhc)) +
  geom_line(alpha = 0.8, linewidth = 1.1) +
  geom_point(size = 2) +   # 👈 puntos reales
  theme_bw() + 
  theme(legend.position = "none") + 
  facet_wrap(~n_samples_cat, scales = "free") + 
  labs(title = "Episodios OXA",
       x = "Día de episodio")

oxa_long_episodes
ggsave(here("img", "oxa_long_episodes.png"), oxa_long_episodes, width = 12, height = 6)


my_palette <- c(
  "#1b9e77", "#d95f02", "#7570b3", "#b2df8a",  "#66a61e",
  "#e6ab02", "#a6761d", "#666666", "#1f78b4", "#e7298a"
)

vim_long_episodes <- df_episodes %>% 
  filter(n_samples_episode > 15, tipo_episodio == "Episodio Positivo") %>% 
  group_by(nhc, patient_episode) %>% 
  filter(any(VIM_fis == 1, na.rm = TRUE)) %>% 
  arrange(nhc, entry_date) %>%
  group_by(patient_episode) %>% 
  ggplot(aes(x = episode_day, y = VIM_fis, group = patient_episode, color=nhc)) +
  geom_line(alpha = 0.8, linewidth = 1.1) + 
  geom_point(size = 2) +  
  theme_bw() + 
  theme(legend.position = "none")+ 
  scale_fill_manual(values = my_palette) + 
  facet_wrap(~n_samples_cat, scale = "free") + labs(title = "Episodios VIM") 
ggsave(here("img", "vim_long_episodes.png"), vim_long_episodes, width = 12, height = 6)


kpc_long_episodes <- df_episodes %>% 
  filter(n_samples_episode > 10, tipo_episodio == "Episodio Positivo") %>% 
  group_by(nhc, patient_episode) %>% 
  filter(any(KPC_fis == 1, na.rm = TRUE)) %>% 
  arrange(nhc, entry_date) %>%
  group_by(patient_episode) %>% 
  ggplot(aes(x = episode_day, y = KPC_fis, group = patient_episode, color=nhc)) +
  geom_line(alpha = 0.8, linewidth = 1.1) + 
   scale_fill_brewer(palette = "Set3") + 
  facet_wrap(~n_samples_cat, scale = "free") + 
  geom_point(size = 2) + labs(title = "Episodios KPC") + theme_bw() + 
  theme(legend.position = "none")

ggsave(here("img", "kpc_long_episodes.png"), kpc_long_episodes, width = 12, height = 6)


ndm_long_episodes <- df_episodes %>% 
  filter(n_samples_episode > 10, tipo_episodio == "Episodio Positivo") %>% 
  group_by(nhc, patient_episode) %>% 
  filter(any(NDM_fis == 1, na.rm = TRUE)) %>% 
  arrange(nhc, entry_date) %>%
  group_by(patient_episode) %>% 
  ggplot(aes(x = episode_day, y = NDM_fis, group = patient_episode, color=nhc)) +
  geom_line(alpha = 0.8, linewidth = 1.1) + 
  geom_point(size = 2) + 
  scale_fill_brewer(palette = "Set3") + 
  facet_wrap(~n_samples_cat, scale = "free") + labs(title = "Episodios NDM") + theme_bw() + theme(legend.position = "none")
  
ggsave(here("img", "ndm_long_episodes.png"), ndm_long_episodes, width = 12, height = 6)
