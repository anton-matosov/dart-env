__author__ = 'yuwenhao'

import numpy as np
from gym import utils
from gym.envs.dart import dart_env
from scipy.optimize import minimize
from pydart2.collision_result import CollisionResult
from pydart2.bodynode import BodyNode
import pydart2.pydart2_api as papi
import random
from random import randrange
import pickle
import copy

from gym.envs.dart.darwin_utils import *

class DartDarwinSquatEnv(dart_env.DartEnv, utils.EzPickle):
    def __init__(self):

        obs_dim = 48

        self.control_bounds = np.array([-np.ones(20, ), np.ones(20, )])

        self.pose_squat_val = np.array([2509, 2297, 1714, 1508, 1816, 2376,
                                    2047, 2171,
                                    2032, 2039, 2795, 568, 1231, 2040,   2041, 2060, 1281, 3525, 2855, 2073])

        self.pose_stand_val = np.array([1500, 2048, 2048, 2500, 2048, 2048,
                                        2048, 2048,
                                        2048, 2048, 2048, 2048, 2048, 2048, 2048, 2048, 2048, 2048, 2048, 2048])

        self.pose_squat = VAL2RADIAN(self.pose_squat_val)
        self.pose_stand = VAL2RADIAN(self.pose_stand_val)

        self.interp_sch = [[0.0, self.pose_squat], [1.0, self.pose_stand]]

        self.delta_angle_scale = 0.5

        self.alive_bonus = 5.0
        self.energy_weight = 0.1
        self.work_weight = 0.01
        self.pose_weight = 0.2

        self.cur_step = 0

        self.torqueLimits = 3.5


        self.t = 0
        # self.dt = 0.002
        self.itr = 0
        self.sol = 0
        self.rankleFlag = False
        self.rhandFlag = False
        self.lhandFlag = False
        self.lankleFlag = False
        self.preverror = np.zeros(26, )
        self.edot = np.zeros(26, )
        self.target = np.zeros(26, )
        self.ndofs = np.zeros(26, )  # self.robot_skeleton.ndofs
        self.tau = np.zeros(26, )
        self.init = np.zeros(26, )
        self.sum = 0
        self.count = 0
        self.dumpTorques = False
        self.dumpActions = False
        self.f1 = np.array([0.])
        self.f2 = np.array([0.])

        self.include_obs_history = 1
        self.include_act_history = 0
        obs_dim *= self.include_obs_history
        obs_dim += len(self.control_bounds[0]) * self.include_act_history
        obs_perm_base = np.array(
            [-3, -4, -5, -0.0001, -1, -2, 6, 7, -14, -15, -16, -17, -18, -19, -8, -9, -10, -11, -12, -13,
             -23, -24, -25, -20, -21, -22, 26, 27, -34, -35, -36, -37, -38, -39, -28, -29, -30, -31, -32, -33,
             -40, 41, 42, -43, 44, -45, 46, -47])
        act_perm_base = np.array(
            [-3, -4, -5, -0.0001, -1, -2, 6, 7, -14, -15, -16, -17, -18, -19, -8, -9, -10, -11, -12, -13])
        self.obs_perm = np.copy(obs_perm_base)

        for i in range(self.include_obs_history - 1):
            self.obs_perm = np.concatenate(
                [self.obs_perm, np.sign(obs_perm_base) * (np.abs(obs_perm_base) + len(self.obs_perm))])
        for i in range(self.include_act_history):
            self.obs_perm = np.concatenate(
                [self.obs_perm, np.sign(act_perm_base) * (np.abs(act_perm_base) + len(self.obs_perm))])
        self.act_perm = np.copy(act_perm_base)

        dart_env.DartEnv.__init__(self, ['darwinmodel/ground1.urdf', 'darwinmodel/darwin_nocollision.URDF', 'darwinmodel/robotis_op2.urdf'], 15, obs_dim,
                                  self.control_bounds, disableViewer=True)

        self.dart_world.set_gravity([0, 0, -9.81])

        self.dupSkel = self.dart_world.skeletons[1]
        self.dupSkel.set_mobile(False)

        self.dart_world.set_collision_detector(0)

        self.robot_skeleton.set_self_collision_check(False)

        self.dart_world.skeletons[0].bodynodes[0].set_friction_coeff(5.0)
        for bn in self.robot_skeleton.bodynodes:
            bn.set_friction_coeff(5.0)

        utils.EzPickle.__init__(self)


    def advance(self, a):
        clamped_control = np.array(a)

        for i in range(len(clamped_control)):
            if clamped_control[i] > self.control_bounds[1][i]:
                clamped_control[i] = self.control_bounds[1][i]
            if clamped_control[i] < self.control_bounds[0][i]:
                clamped_control[i] = self.control_bounds[0][i]

        self.ref_target = self.interp_sch[0][1]

        for i in range(len(self.interp_sch)-1):
            if self.t >= self.interp_sch[i][0] and self.t < self.interp_sch[i+1][0]:
                ratio = (self.t - self.interp_sch[i][0]) / (self.interp_sch[i+1][0] - self.interp_sch[i][0])
                self.ref_target = ratio * self.interp_sch[i+1][1] + (1 - ratio) * self.interp_sch[i][1]
        if self.t > self.interp_sch[-1][0]:
            self.ref_target = self.interp_sch[-1][1]

        self.target[6:] = self.ref_target + clamped_control * self.delta_angle_scale
        self.target[6:] = np.clip(self.target[6:], JOINT_LOW_BOUND, JOINT_UP_BOUND)


        self.dupSkel.set_positions(self.target)
        self.dupSkel.set_velocities(self.target*0)

        for i in range(self.frame_skip):

            self.tau[6:] = self.PID()
            self.tau[0:6] *= 0.0

            if self.dumpTorques:
                with open("torques.txt", "ab") as fp:
                    np.savetxt(fp, np.array([self.tau]), fmt='%1.5f')

            if self.dumpActions:
                with open("targets_from_net.txt", 'ab') as fp:
                    np.savetxt(fp, np.array([[self.target[6], self.robot_skeleton.q[6]]]), fmt='%1.5f')

            self.robot_skeleton.set_forces(self.tau)
            self.dart_world.step()


    def PID(self):
        # print("#########################################################################3")

        self.kp = [2.1, 1.79, 4.93,
                   2.0, 2.02, 1.98,
                   2.2, 2.06,
                   148, 152, 150, 136, 153, 102,
                   151, 151.4, 150.45, 151.36, 154, 105.2]
        self.kd = [0.21, 0.23, 0.22,
                   0.25, 0.21, 0.26,
                   0.28, 0.213
            , 0.192, 0.198, 0.22, 0.199, 0.02, 0.01,
                   0.53, 0.27, 0.21, 0.205, 0.022, 0.056]

        self.kp = [item for item in self.kp]
        self.kd = [item for item in self.kd]

        q = self.robot_skeleton.q
        qdot = self.robot_skeleton.dq
        tau = np.zeros(26, )
        for i in range(6, 26):
            # print(q.shape)
            #self.edot[i] = ((q[i] - self.target[i]) -
            #                self.preverror[i]) / self.dt
            tau[i] = -self.kp[i - 6] * \
                     (q[i] - self.target[i]) - \
                     self.kd[i - 6] * qdot[i]
            #self.preverror[i] = (q[i] - self.target[i])

        torqs = self.ClampTorques(tau)

        return torqs[6:]

    def ClampTorques(self, torques):
        torqueLimits = self.torqueLimits

        for i in range(6, 26):
            if torques[i] > torqueLimits:  #
                torques[i] = torqueLimits
            if torques[i] < -torqueLimits:
                torques[i] = -torqueLimits

        return torques

    def step(self, a):
        self.advance(a)

        pose_math_rew = np.sum(
            np.abs(np.array(self.ref_target - self.robot_skeleton.q[6:])) ** 2)

        reward = -self.energy_weight * np.sum(
            a) ** 2 + self.alive_bonus - pose_math_rew * self.pose_weight
        reward -= self.work_weight * np.dot(self.tau, self.robot_skeleton.dq)

        s = self.state_vector()
        com_height = self.robot_skeleton.bodynodes[0].com()[2]
        done = not (np.isfinite(s).all() and (np.abs(s[2:]) < 200).all())

        self.fall_on_ground = False
        contacts = self.dart_world.collision_result.contacts
        total_force_mag = 0
        permitted_contact_bodies = [self.robot_skeleton.bodynodes[-1], self.robot_skeleton.bodynodes[-2],
                                    self.robot_skeleton.bodynodes[-7], self.robot_skeleton.bodynodes[-8]]
        for contact in contacts:
            total_force_mag += np.square(contact.force).sum()
            if contact.bodynode1 not in permitted_contact_bodies and contact.bodynode2 not in permitted_contact_bodies:
                self.fall_on_ground = True
        if self.fall_on_ground:
            done = True

        if done:
            reward = 0

        self.t += self.dt * 1.0
        self.cur_step += 1

        ob = self._get_obs()

        return ob, reward, done, {}

    def _get_obs(self):
        # phi = np.array([self.count/self.ref_traj.shape[0]])
        # print(ContactList.shape)
        state = np.concatenate([self.robot_skeleton.q[6:], self.robot_skeleton.dq[6:]])

        body_com = self.robot_skeleton.bodynode('MP_BODY').C

        bodyvel = np.dot(self.robot_skeleton.bodynode('MP_BODY').world_jacobian(
            offset=self.robot_skeleton.bodynode('MP_BODY').local_com()), self.robot_skeleton.dq)
        state = np.concatenate([state, body_com[1:], bodyvel])

        return state

    def reset_model(self):
        self.dart_world.reset()
        qpos = np.zeros(
            self.robot_skeleton.ndofs)  # self.robot_skeleton.q + self.np_random.uniform(low=-.005, high=.005, size=self.robot_skeleton.ndofs)
        qvel = self.robot_skeleton.dq + self.np_random.uniform(low=-.005, high=.005,
                                                               size=self.robot_skeleton.ndofs)  # np.zeros(self.robot_skeleton.ndofs) #

        qpos[1] = 0.20
        qpos[5] = -0.46

        # LEFT HAND
        qpos[6:] = self.interp_sch[0][1]


        qpos[6:] += np.random.uniform(low=-0.01, high=0.01, size=20)
        self.init_q = np.copy(qpos)
        # self.target = qpos
        self.count = 0
        self.set_state(qpos, qvel)
        f1 = np.random.uniform(low=-15, high=15., size=1)
        f2 = np.random.uniform(low=-15, high=15., size=1)
        #self.robot_skeleton.bodynodes[0].add_ext_force([f1, f2, 0], [0, 0, 0.0])
        self.t = 0
        for i in range(6, self.robot_skeleton.ndofs):
            j = self.robot_skeleton.dof(i)
            j.set_damping_coefficient(0.515)
        self.init_position_x = self.robot_skeleton.bodynode('MP_BODY').C[0]
        self.init_footheight = -0.339
        return self._get_obs()

    def viewer_setup(self):
        if not self.disableViewer:
            self._get_viewer().scene.tb.trans[0] = 0.0
            self._get_viewer().scene.tb.trans[2] = -1.5
            self._get_viewer().scene.tb.trans[1] = 0.0
            self._get_viewer().scene.tb.theta = 80
            self._get_viewer().scene.tb.phi = 0

        return 0
